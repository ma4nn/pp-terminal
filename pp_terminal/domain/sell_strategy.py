"""
    Copyright (C) 2025-26 Dipl.-Ing. Christoph Massmann <chris@dev-investor.de>

    This file is part of pp-terminal.

    pp-terminal is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    pp-terminal is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with pp-terminal. If not, see <http://www.gnu.org/licenses/>.
"""

import heapq
from abc import ABC, abstractmethod
from typing import cast

import pandas as pd
from pandera.typing import DataFrame

from pp_terminal.domain.schemas import TaxLotSellSchema, Money
from pp_terminal.exceptions import InputError


class SellStrategy(ABC):  # pylint: disable=too-few-public-methods
    @abstractmethod
    def select_lots(self, lots: DataFrame[TaxLotSellSchema]) -> DataFrame[TaxLotSellSchema]:
        ...


class FixedSharesStrategy(SellStrategy):  # pylint: disable=too-few-public-methods
    def __init__(self, shares: float):
        self.shares = shares

    def select_lots(self, lots: DataFrame[TaxLotSellSchema]) -> DataFrame[TaxLotSellSchema]:
        cumsum = lots['shares'].cumsum()
        prev_cumsum = cumsum.shift(1, fill_value=0.0)

        shares_taken = (self.shares - prev_cumsum).clip(lower=0, upper=lots['shares'])

        contributing_mask = shares_taken > 0
        if not contributing_mask.any():
            raise InputError(f"Insufficient shares available. Requested: {self.shares}, Available: 0")

        df = lots[contributing_mask].copy()
        df['shares'] = shares_taken[contributing_mask].values

        total_allocated = df['shares'].sum()
        if total_allocated < self.shares - 0.0001:
            raise InputError(f"Insufficient shares available. Requested: {self.shares}, Available: {total_allocated}")

        return TaxLotSellSchema.validate(df)


def _tax_priority(row: pd.Series) -> float:
    return row['totalTax'] / row['netProceeds'] if row['netProceeds'] > 0 else float('inf')


def _build_fifo_queues(df: pd.DataFrame) -> dict[tuple[str, str], list[int]]:
    groups: dict[tuple[str, str], list[int]] = {}
    for idx, row in df.iterrows():
        groups.setdefault((row['accountId'], row['securityId']), []).append(idx)
    return groups


def _consume_lots_by_measure(  # pylint: disable=too-many-locals
        df: pd.DataFrame, value_col: str, per_share_col: str, target: float, skip_worthless: bool = False
) -> pd.DataFrame:
    """Fill `target`, measured in `value_col` (grossProceeds or netProceeds), drawing the most tax-efficient
    lots first and FIFO within each (account, security). With `skip_worthless`, lots whose `per_share_col`
    is non-positive are never sold (they cannot bring a net target closer)."""
    queues = _build_fifo_queues(df)
    heap: list[tuple[float, int, int]] = []
    tie = 0
    for queue in queues.values():
        head = df.loc[queue[0]]
        if not skip_worthless or head[per_share_col] > 0:
            heapq.heappush(heap, (_tax_priority(head), tie, queue[0]))
            tie += 1

    taken: dict[int, float] = {}
    remaining = target
    while remaining > 0.005 and heap:
        _priority, _tie, row_idx = heapq.heappop(heap)
        row = df.loc[row_idx]

        if row[value_col] <= remaining + 0.005:
            taken[row_idx] = row['shares']
            remaining -= row[value_col]
        else:
            taken[row_idx] = min(remaining / row[per_share_col], row['shares'])
            remaining = 0.0

        queue = queues[(row['accountId'], row['securityId'])]
        pos = queue.index(row_idx)
        if pos + 1 < len(queue):
            next_row = df.loc[queue[pos + 1]]
            if not skip_worthless or next_row[per_share_col] > 0:
                heapq.heappush(heap, (_tax_priority(next_row), tie, queue[pos + 1]))
                tie += 1

    result = df.loc[list(taken.keys())].copy()
    result['shares'] = list(taken.values())
    return result


def _consume_group_lots(group: pd.DataFrame, target_gross: float) -> pd.DataFrame:
    """Fill a group's gross quota drawing the most tax-efficient lots first (lot-level, no order floor)."""
    return _consume_lots_by_measure(group, 'grossProceeds', 'salePrice', target_gross)


class MinTaxStrategy(SellStrategy):  # pylint: disable=too-few-public-methods
    def __init__(self, target_net: Money):
        self.target_net = target_net

    def select_lots(self, lots: DataFrame[TaxLotSellSchema]) -> DataFrame[TaxLotSellSchema]:
        if lots.empty:
            raise InputError(f"No lots available. Target net: {self.target_net:.2f}")

        df = lots.reset_index()

        max_achievable = df.loc[df['netProceedsPerShare'] > 0, 'netProceeds'].sum()
        if self.target_net > max_achievable + 0.005:
            raise InputError(
                f"Target net {self.target_net:.2f} exceeds maximum achievable {max_achievable:.2f}"
            )

        selected = _consume_lots_by_measure(df, 'netProceeds', 'netProceedsPerShare', self.target_net, skip_worthless=True)
        return TaxLotSellSchema.validate(
            selected.set_index(['date', 'accountId', 'securityId'])
        )


class TargetGrossStrategy(SellStrategy):  # pylint: disable=too-few-public-methods
    """Reach a target gross proceeds amount, drawing the most tax-efficient lots first (FIFO within each holding)."""

    def __init__(self, target_gross: Money):
        self.target_gross = target_gross

    def select_lots(self, lots: DataFrame[TaxLotSellSchema]) -> DataFrame[TaxLotSellSchema]:
        if lots.empty:
            raise InputError(f"No lots available. Target gross: {self.target_gross:.2f}")

        df = lots.reset_index()
        max_achievable = df['grossProceeds'].sum()
        if self.target_gross > max_achievable + 0.005:
            raise InputError(
                f"Target gross {self.target_gross:.2f} exceeds maximum achievable {max_achievable:.2f}"
            )

        selected = _consume_lots_by_measure(df, 'grossProceeds', 'salePrice', self.target_gross)
        return TaxLotSellSchema.validate(
            selected.set_index(['date', 'accountId', 'securityId'])
        )


class AllocationPreservingStrategy(SellStrategy):  # pylint: disable=too-few-public-methods
    """Reach the target (net or gross proceeds) while preserving the current allocation.

    Every allocation group loses the same fraction of its value, so weights stay
    intact. A group is a taxonomy category when ``category_by_security`` maps the
    security, otherwise the security itself (security-level preservation). Within a
    group the required value is drawn from the most tax-efficient securities first
    (FIFO within each), consolidating redundant holdings of the same asset class
    instead of selling every one pro-rata.

    Pass exactly one of ``target_net`` or ``target_gross``. A gross target liquidates
    that market value (each class shedding the same fraction of its gross value); a net
    target hits that spendable amount after tax.

    When ``min_amount`` is set it acts as a per-order (per account/security) floor:
    within a group the quota is consolidated onto whole orders that each clear the
    floor, and a holding too small to ever form a valid order is left unsold (its
    class quota is covered by larger siblings, so weights hold). A group is dropped
    altogether when its whole quota is below the floor, or when none of its holdings
    is large enough to fill even one order (so it could never contribute); the
    remaining groups still absorb the full target, so those small classes drift
    slightly, and their names are exposed via ``excluded_groups`` after ``select_lots``
    so the caller can warn about them.
    """

    def __init__(self, target_net: Money | None = None, category_by_security: dict[str, str] | None = None,
                 min_amount: Money | None = None, target_gross: Money | None = None):
        if (target_net is None) == (target_gross is None):
            raise InputError("provide exactly one of a target net or a target gross amount")
        self._by_gross = target_gross is not None
        self._target: Money = cast(Money, target_gross if self._by_gross else target_net)
        self.category_by_security = category_by_security or {}
        self.min_amount = min_amount
        self.excluded_groups: list[str] = []

    @property
    def _target_label(self) -> str:
        return 'gross' if self._by_gross else 'net'

    def _achieved(self, selected: pd.DataFrame) -> float:
        """Amount the selection realizes, in the active target's units (gross proceeds or net proceeds)."""
        per_share = selected['salePrice'] if self._by_gross else selected['netProceedsPerShare']
        return float((selected['shares'] * per_share).sum())

    def select_lots(self, lots: DataFrame[TaxLotSellSchema]) -> DataFrame[TaxLotSellSchema]:
        if lots.empty:
            raise InputError(f"No lots available. Target {self._target_label}: {self._target:.2f}")

        df = lots.reset_index().sort_values(['accountId', 'securityId', 'date'])
        df['_group'] = df['securityId'].map(lambda sid: self.category_by_security.get(sid, sid))

        max_achievable = self._achieved(df)
        if self._target > max_achievable + 0.005:
            raise InputError(
                f"Target {self._target_label} {self._target:.2f} exceeds maximum achievable {max_achievable:.2f}"
            )

        sellable, fraction = self._resolve_included_groups(df)
        selected = self._select_at_fraction(sellable, fraction)
        return TaxLotSellSchema.validate(
            selected.drop(columns='_group').set_index(['date', 'accountId', 'securityId'])
        )

    def _resolve_included_groups(self, df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
        """Drop groups too small to sell, redistributing to hit the target.

        A group is dropped when its whole quota falls below one order, or when no single holding in it is large
        enough to form a valid order (so it could never contribute under the per-order floor). Dropped groups are
        reported via ``excluded_groups`` rather than silently left unsold.
        """
        self.excluded_groups = []
        if not self.min_amount:
            return df, self._solve_fraction(df)

        group_gross = df.groupby('_group', sort=False)['grossProceeds'].sum()
        order_gross = df.groupby(['_group', 'accountId', 'securityId'], sort=False)['grossProceeds'].sum()
        largest_order = order_gross.groupby(level='_group', sort=False).max()
        included, self.excluded_groups = self._partition_sellable_groups(group_gross.index, largest_order)
        while True:
            current = df[df['_group'].isin(included)]
            if current.empty or self._achieved(current) < self._target - 0.005:
                raise InputError(
                    f"Reaching a {self._target_label} of {self._target:.2f} requires selling amounts below the "
                    f"{self.min_amount:.2f} minimum; lower --target-{self._target_label} or the minimum"
                )
            fraction = self._solve_fraction(current)
            below = [group for group in included if fraction * group_gross[group] < self.min_amount - 0.005]
            if not below:
                return current, fraction
            smallest = min(below, key=lambda group: group_gross[group])
            included.remove(smallest)
            self.excluded_groups.append(str(smallest))

    def _partition_sellable_groups(self, groups: pd.Index, largest_order: pd.Series) -> tuple[list[str], list[str]]:
        included, excluded = [], []
        for group in groups:
            # a group whose biggest holding cannot fill one order can never contribute under the floor
            if largest_order[group] < (self.min_amount or 0.0) - 0.005:
                excluded.append(str(group))
            else:
                included.append(group)
        return included, excluded

    def _solve_fraction(self, df: pd.DataFrame) -> float:
        low, high = 0.0, 1.0
        for _ in range(60):
            mid = (low + high) / 2
            selected = self._select_at_fraction(df, mid)
            if self._achieved(selected) < self._target:
                low = mid
            else:
                high = mid
        return high

    def _select_at_fraction(self, df: pd.DataFrame, fraction: float) -> pd.DataFrame:
        parts = []
        for _group, group in df.groupby('_group', sort=False):
            target_gross = fraction * group['grossProceeds'].sum()
            selected = self._consume_group_to_gross(group, target_gross)
            if not selected.empty:
                parts.append(selected)
        return pd.concat(parts) if parts else df.iloc[0:0].copy()

    def _consume_group_to_gross(self, group: pd.DataFrame, target_gross: float) -> pd.DataFrame:
        if self.min_amount:
            return self._consume_group_with_floor(group, target_gross, self.min_amount)
        return _consume_group_lots(group, target_gross)

    @staticmethod
    def _consume_group_with_floor(group: pd.DataFrame, target_gross: float, floor: float) -> pd.DataFrame:
        """Fill a group's quota consolidating onto whole orders that each clear the per-order floor."""
        keys = AllocationPreservingStrategy._order_keys_by_tax(group)
        allocations = AllocationPreservingStrategy._allocate_gross(keys, target_gross, floor)
        lots_by_key = {key: fifo_idx for key, _capacity, _priority, fifo_idx in keys}
        parts = [AllocationPreservingStrategy._consume_key_fifo(group, lots_by_key[key], gross)
                 for key, gross in allocations.items()]
        return pd.concat(parts) if parts else group.iloc[0:0].copy()

    @staticmethod
    def _order_keys_by_tax(group: pd.DataFrame) -> list[tuple[tuple[str, str], float, float, list[int]]]:
        """One entry per order (accountId, securityId): its gross capacity, blended tax priority and FIFO lots."""
        keys: list[tuple[tuple[str, str], float, float, list[int]]] = []
        for key, sub in group.groupby(['accountId', 'securityId'], sort=False):
            net = sub['netProceeds'].sum()
            priority = sub['totalTax'].sum() / net if net > 0 else float('inf')
            fifo_idx = list(sub.sort_values('date').index)
            keys.append((cast(tuple[str, str], key), sub['grossProceeds'].sum(), priority, fifo_idx))
        keys.sort(key=lambda item: (item[2], -item[1]))
        return keys

    @staticmethod
    def _allocate_gross(
            keys: list[tuple[tuple[str, str], float, float, list[int]]], target_gross: float, floor: float
    ) -> dict[tuple[str, str], float]:
        remaining = target_gross
        allocations: dict[tuple[str, str], float] = {}
        for key, capacity, _priority, _fifo_idx in keys:
            if remaining <= 0.005:
                break
            if capacity < floor - 0.005:
                continue  # even a full sale of this holding stays below the floor -> never a valid order
            take = min(capacity, remaining)
            allocations[key] = take
            remaining -= take
        AllocationPreservingStrategy._raise_marginal_to_floor(allocations, floor)
        return allocations

    @staticmethod
    def _raise_marginal_to_floor(allocations: dict[tuple[str, str], float], floor: float) -> None:
        """Lift the single sub-floor partial order to the floor by shifting the shortfall onto larger orders."""
        for key in [key for key, gross in allocations.items() if gross < floor - 0.005]:
            deficit = floor - allocations[key]
            donors = sorted((other for other in allocations if other != key and allocations[other] - floor > 0.005),
                            key=lambda other: allocations[other], reverse=True)
            if sum(allocations[other] - floor for other in donors) >= deficit - 0.005:
                allocations[key] = floor
                for donor in donors:
                    shifted = min(allocations[donor] - floor, deficit)
                    allocations[donor] -= shifted
                    deficit -= shifted
                    if deficit <= 0.005:
                        break
            else:
                del allocations[key]  # cannot form a valid order here; leave this sliver (< floor) unsold

    @staticmethod
    def _consume_key_fifo(group: pd.DataFrame, fifo_idx: list[int], target_gross: float) -> pd.DataFrame:
        taken: dict[int, float] = {}
        remaining = target_gross
        for idx in fifo_idx:
            if remaining <= 0.005:
                break
            row = group.loc[idx]
            if row['grossProceeds'] <= remaining + 0.005:
                taken[idx] = row['shares']
                remaining -= row['grossProceeds']
            else:
                taken[idx] = remaining / row['salePrice']
                remaining = 0.0
        result = group.loc[list(taken.keys())].copy()
        result['shares'] = list(taken.values())
        return result

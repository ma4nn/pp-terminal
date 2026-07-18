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


def _seed_heap(df: pd.DataFrame, groups: dict[tuple[str, str], list[int]]) -> list[tuple[float, int, int]]:
    heap: list[tuple[float, int, int]] = []
    for tie, queue in enumerate(groups.values()):
        row = df.loc[queue[0]]
        if row['netProceedsPerShare'] > 0:
            heapq.heappush(heap, (_tax_priority(row), tie, queue[0]))
    return heap


class MinTaxStrategy(SellStrategy):  # pylint: disable=too-few-public-methods
    def __init__(self, target_net: Money):
        self.target_net = target_net

    def select_lots(self, lots: DataFrame[TaxLotSellSchema]) -> DataFrame[TaxLotSellSchema]:  # pylint: disable=too-many-locals
        if lots.empty:
            raise InputError(f"No lots available. Target net: {self.target_net:.2f}")

        df = lots.reset_index()

        max_achievable = df.loc[df['netProceedsPerShare'] > 0, 'netProceeds'].sum()
        if self.target_net > max_achievable + 0.005:
            raise InputError(
                f"Target net {self.target_net:.2f} exceeds maximum achievable {max_achievable:.2f}"
            )

        groups = _build_fifo_queues(df)
        heap = _seed_heap(df, groups)
        selected = self._consume_lots(df, groups, heap)

        result = df.loc[list(selected.keys())].copy()
        result['shares'] = list(selected.values())
        return TaxLotSellSchema.validate(
            result.set_index(['date', 'accountId', 'securityId'])
        )

    def _consume_lots(
            self,
            df: pd.DataFrame,
            groups: dict[tuple[str, str], list[int]],
            heap: list[tuple[float, int, int]]
    ) -> dict[int, float]:
        remaining = self.target_net
        selected: dict[int, float] = {}
        tie = len(groups)

        while remaining > 0.005 and heap:
            _priority, _tie, row_idx = heapq.heappop(heap)
            row = df.loc[row_idx]

            if row['netProceedsPerShare'] <= 0:
                continue

            if row['netProceeds'] <= remaining + 0.005:
                selected[row_idx] = row['shares']
                remaining -= row['netProceeds']
            else:
                selected[row_idx] = min(remaining / row['netProceedsPerShare'], row['shares'])
                remaining = 0.0

            queue = groups[(row['accountId'], row['securityId'])]
            pos = queue.index(row_idx)
            if pos + 1 < len(queue):
                next_row = df.loc[queue[pos + 1]]
                if next_row['netProceedsPerShare'] > 0:
                    heapq.heappush(heap, (_tax_priority(next_row), tie, queue[pos + 1]))
                    tie += 1

        return selected


class AllocationPreservingStrategy(SellStrategy):  # pylint: disable=too-few-public-methods
    """Reach the target net while preserving the current allocation.

    Every allocation group loses the same fraction of its value, so weights stay
    intact. A group is a taxonomy category when ``category_by_security`` maps the
    security, otherwise the security itself (security-level preservation). Within a
    group the required value is drawn from the most tax-efficient securities first
    (FIFO within each), consolidating redundant holdings of the same asset class
    instead of selling every one pro-rata.

    When ``min_amount`` is set, any group whose proportional quota (gross proceeds)
    would fall below it is left unsold to avoid dust trades; the remaining groups
    still absorb the full target net, so those small classes drift slightly. The
    names of those skipped groups are exposed via ``excluded_groups`` after
    ``select_lots`` so the caller can warn about them.
    """

    def __init__(self, target_net: Money, category_by_security: dict[str, str] | None = None,
                 min_amount: Money | None = None):
        self.target_net = target_net
        self.category_by_security = category_by_security or {}
        self.min_amount = min_amount
        self.excluded_groups: list[str] = []

    def select_lots(self, lots: DataFrame[TaxLotSellSchema]) -> DataFrame[TaxLotSellSchema]:
        if lots.empty:
            raise InputError(f"No lots available. Target net: {self.target_net:.2f}")

        df = lots.reset_index().sort_values(['accountId', 'securityId', 'date'])
        df['_group'] = df['securityId'].map(lambda sid: self.category_by_security.get(sid, sid))

        max_net = (df['shares'] * df['netProceedsPerShare']).sum()
        if self.target_net > max_net + 0.005:
            raise InputError(
                f"Target net {self.target_net:.2f} exceeds maximum achievable {max_net:.2f}"
            )

        sellable, fraction = self._resolve_included_groups(df)
        selected = self._select_at_fraction(sellable, fraction)
        return TaxLotSellSchema.validate(
            selected.drop(columns='_group').set_index(['date', 'accountId', 'securityId'])
        )

    def _resolve_included_groups(self, df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
        """Drop groups too small to sell (quota < min_amount), redistributing to hit the target."""
        self.excluded_groups = []
        if not self.min_amount:
            return df, self._solve_fraction(df)

        group_gross = df.groupby('_group', sort=False)['grossProceeds'].sum()
        included = list(group_gross.index)
        while True:
            current = df[df['_group'].isin(included)]
            if (current['shares'] * current['netProceedsPerShare']).sum() < self.target_net - 0.005:
                raise InputError(
                    f"Reaching a net of {self.target_net:.2f} requires selling amounts below the "
                    f"{self.min_amount:.2f} minimum; lower --target-net or the minimum"
                )
            fraction = self._solve_fraction(current)
            below = [group for group in included if fraction * group_gross[group] < self.min_amount - 0.005]
            if not below:
                return current, fraction
            smallest = min(below, key=lambda group: group_gross[group])
            included.remove(smallest)
            self.excluded_groups.append(str(smallest))

    def _solve_fraction(self, df: pd.DataFrame) -> float:
        low, high = 0.0, 1.0
        for _ in range(60):
            mid = (low + high) / 2
            selected = self._select_at_fraction(df, mid)
            net = (selected['shares'] * selected['netProceedsPerShare']).sum()
            if net < self.target_net:
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

    @staticmethod
    def _consume_group_to_gross(group: pd.DataFrame, target_gross: float) -> pd.DataFrame:  # pylint: disable=too-many-locals
        queues = _build_fifo_queues(group)
        heap: list[tuple[float, int, int]] = []
        for tie, queue in enumerate(queues.values()):
            head = group.loc[queue[0]]
            heapq.heappush(heap, (_tax_priority(head), tie, queue[0]))

        taken: dict[int, float] = {}
        remaining = target_gross
        tie = len(queues)
        while remaining > 0.005 and heap:
            _priority, _tie, row_idx = heapq.heappop(heap)
            row = group.loc[row_idx]

            if row['grossProceeds'] <= remaining + 0.005:
                taken[row_idx] = row['shares']
                remaining -= row['grossProceeds']
            else:
                taken[row_idx] = remaining / row['salePrice']
                remaining = 0.0

            queue = queues[(row['accountId'], row['securityId'])]
            pos = queue.index(row_idx)
            if pos + 1 < len(queue):
                next_row = group.loc[queue[pos + 1]]
                heapq.heappush(heap, (_tax_priority(next_row), tie, queue[pos + 1]))
                tie += 1

        result = group.loc[list(taken.keys())].copy()
        result['shares'] = list(taken.values())
        return result

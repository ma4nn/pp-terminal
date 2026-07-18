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

from datetime import datetime
from typing import Any

import pandas as pd
import pytest
from pandera.typing import DataFrame

from pp_terminal.domain.cost_basis import enrich_fifo_lots, finalize_sell_lots
from pp_terminal.domain.sell_strategy import FixedSharesStrategy, MinTaxStrategy, AllocationPreservingStrategy
from pp_terminal.domain.schemas import AccountType, TransactionType, TaxLotSellSchema
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.data.filters import filter_by_account_and_security
from pp_terminal.exceptions import InputError
from tests.conftest import TAX_RATE


def _make_portfolio(transactions_data: list[Any], accounts_data: list[Any] | None = None, securities_data: list[Any] | None = None) -> Portfolio:
    if accounts_data is None:
        accounts_data = [['Depot1', AccountType.SECURITIES.value, None, False, 'EUR']]
    accounts = pd.DataFrame(
        accounts_data,
        columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
        index=[f'acc-{i+1}' for i in range(len(accounts_data))]
    )
    accounts.index.name = 'accountId'

    if securities_data is None:
        securities_data = [['ETF A', 'WKN1', 'EUR']]
    securities = pd.DataFrame(
        securities_data,
        columns=['name', 'wkn', 'currency'],
        index=[f'sec-{i+1}' for i in range(len(securities_data))]
    )
    securities.index.name = 'securityId'

    transactions = pd.DataFrame(
        transactions_data,
        columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees']
    ).set_index(['date', 'accountId', 'securityId'])

    prices = pd.DataFrame(columns=['date', 'securityId', 'price']).set_index(['date', 'securityId'])

    return Portfolio(accounts, transactions, securities, prices)


def _enrich(portfolio: Portfolio, sell_price: float, acc_id: str = 'acc-1', sec_id: str = 'sec-1',
            sell_date: datetime | None = None) -> DataFrame[TaxLotSellSchema]:
    if sell_date is None:
        sell_date = datetime(2025, 1, 1)
    snapshot = PortfolioSnapshot(portfolio, sell_date)
    transactions = snapshot.securities_account_transactions.pipe(
        filter_by_account_and_security, account_id=acc_id, security_id=sec_id
    )
    return enrich_fifo_lots(transactions, sell_date, sell_price, TAX_RATE)


def _enrich_multi(portfolio: Portfolio, sell_price: float, sell_date: datetime | None = None) -> DataFrame[TaxLotSellSchema]:
    if sell_date is None:
        sell_date = datetime(2025, 1, 1)
    snapshot = PortfolioSnapshot(portfolio, sell_date)
    holdings = snapshot.shares
    all_enriched = []
    for (acc_id, sec_id, _currency), _ in holdings.items():
        transactions = snapshot.securities_account_transactions.pipe(
            filter_by_account_and_security, account_id=acc_id, security_id=sec_id
        )
        enriched = enrich_fifo_lots(transactions, sell_date, sell_price, TAX_RATE)
        if not enriched.empty:
            all_enriched.append(enriched)
    return pd.concat(all_enriched) if all_enriched else TaxLotSellSchema.empty()


# --- FixedSharesStrategy ---

class TestFixedSharesStrategy:
    def test_single_lot_partial(self) -> None:
        portfolio = _make_portfolio([
            [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 10000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        ])
        enriched = _enrich(portfolio, sell_price=150.0)
        result = FixedSharesStrategy(30.0).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        assert len(result) == 1
        assert result.iloc[0]['shares'] == pytest.approx(30.0)
        assert result.iloc[0]['grossProceeds'] == pytest.approx(4500.0)

    def test_spanning_multiple_lots(self) -> None:
        portfolio = _make_portfolio([
            [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 50.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            [datetime(2021, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 7000.0, 50.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        ])
        enriched = _enrich(portfolio, sell_price=160.0)
        result = FixedSharesStrategy(70.0).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        assert len(result) == 2
        assert result.iloc[0]['shares'] == pytest.approx(50.0)
        assert result.iloc[1]['shares'] == pytest.approx(20.0)

    def test_exact_match(self) -> None:
        portfolio = _make_portfolio([
            [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 50.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        ])
        enriched = _enrich(portfolio, sell_price=160.0)
        result = FixedSharesStrategy(50.0).select_lots(enriched)

        assert len(result) == 1
        assert result.iloc[0]['shares'] == pytest.approx(50.0)

    def test_insufficient_shares_raises(self) -> None:
        portfolio = _make_portfolio([
            [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 50.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        ])
        enriched = _enrich(portfolio, sell_price=160.0)

        with pytest.raises(InputError, match="Insufficient shares"):
            FixedSharesStrategy(100.0).select_lots(enriched)


# --- MinTaxStrategy ---

class TestMinTaxStrategy:
    def test_picks_lowest_tax_rate_lot(self) -> None:
        """Given two securities with different gains, picks the one with lower effective tax."""
        portfolio = _make_portfolio(
            transactions_data=[
                # sec-1: bought at 50, sell at 100 -> high gain -> high tax
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                # sec-2: bought at 90, sell at 100 -> low gain -> low tax
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 9000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR']],
        )
        enriched = _enrich_multi(portfolio, sell_price=100.0)

        # Target net small enough to be satisfied by sec-2 alone
        result = MinTaxStrategy(500.0).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        # Should pick sec-2 (lower tax rate) first
        sec_ids = result.reset_index()['securityId'].unique()
        assert 'sec-2' in sec_ids

    def test_picks_underwater_lot_first(self) -> None:
        """Lots at a loss (0 tax) should be preferred over profitable lots."""
        portfolio = _make_portfolio(
            transactions_data=[
                # sec-1: bought at 50, sell at 100 -> profitable
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                # sec-2: bought at 120, sell at 100 -> loss (0 tax, but positive net proceeds)
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 12000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR']],
        )
        enriched = _enrich_multi(portfolio, sell_price=100.0)

        # sec-2 has 0 tax -> effective rate 0 -> should be picked first
        result = MinTaxStrategy(500.0).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        sec_ids = result.reset_index()['securityId'].unique().tolist()
        assert sec_ids == ['sec-2']

    def test_respects_fifo_within_group(self) -> None:
        """Within a single (account, security), must consume lots in FIFO order."""
        portfolio = _make_portfolio(
            transactions_data=[
                # Lot 1: bought at 90 -> small gain
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 900.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                # Lot 2: bought at 50 -> large gain (but can't be accessed before lot 1)
                [datetime(2021, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 500.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
        )
        enriched = _enrich(portfolio, sell_price=100.0)

        # Need more net than lot 1 provides -> must consume lot 1 first, then lot 2
        lot1_net = enriched.iloc[0]['netProceeds']
        target = lot1_net + 100.0  # force spill into lot 2
        result = MinTaxStrategy(target).select_lots(enriched)

        assert len(result) == 2
        # First lot (FIFO) must be fully consumed
        assert result.iloc[0]['shares'] == pytest.approx(10.0)

    def test_cross_security_selection(self) -> None:
        """MinTaxStrategy selects the best lots across multiple securities."""
        portfolio = _make_portfolio(
            transactions_data=[
                # sec-1: bought at 80 -> moderate gain
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 8000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                # sec-2: bought at 95 -> small gain
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 9500.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR']],
        )
        enriched = _enrich_multi(portfolio, sell_price=100.0)

        # Target large enough to require both securities
        max_net = enriched['netProceeds'].sum()
        result = MinTaxStrategy(max_net - 1.0).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        sec_ids = result.reset_index()['securityId'].unique()
        assert len(sec_ids) == 2

    def test_partial_lot_for_exact_target(self) -> None:
        """Strategy should partially consume the final lot to hit the target."""
        portfolio = _make_portfolio([
            [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 10000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        ])
        enriched = _enrich(portfolio, sell_price=150.0)

        # Target net that requires partial consumption
        full_net = enriched.iloc[0]['netProceeds']
        target = full_net / 2
        result = MinTaxStrategy(target).select_lots(enriched)

        assert len(result) == 1
        assert result.iloc[0]['shares'] < 100.0
        assert result.iloc[0]['shares'] > 0.0

    def test_target_exceeds_max_raises(self) -> None:
        """Should raise InputError with max achievable amount in message."""
        portfolio = _make_portfolio([
            [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 10000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        ])
        enriched = _enrich(portfolio, sell_price=150.0)

        with pytest.raises(InputError, match="exceeds maximum achievable"):
            MinTaxStrategy(999999.0).select_lots(enriched)

    def test_skips_lots_with_zero_net_proceeds_per_share(self) -> None:
        """Lots where netProceedsPerShare <= 0 should be skipped."""
        portfolio = _make_portfolio(
            transactions_data=[
                # sec-1: bought at 100, sell at 100 with very high tax scenario doesn't exist naturally.
                # Instead: sec-1 bought at 200, sell at 100 -> loss, but netProceeds = grossProceeds - 0 tax = positive
                # Actually netProceedsPerShare = netProceeds/shares which is always gross - tax / shares.
                # For nps <= 0 we need grossProceeds <= totalTax which can't happen with standard tax rates.
                # So test with a security where sale price is 0 -> grossProceeds = 0 -> nps = 0
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 10000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 5000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR']],
        )
        # sec-1 at price 100, sec-2 at price 100
        sell_date = datetime(2025, 1, 1)
        snapshot = PortfolioSnapshot(portfolio, sell_date)
        holdings = snapshot.shares

        all_enriched = []
        for (acc_id, sec_id, _currency), _ in holdings.items():
            transactions = snapshot.securities_account_transactions.pipe(
                filter_by_account_and_security, account_id=acc_id, security_id=sec_id
            )
            # sec-1 sell at 0 (nps=0), sec-2 sell at 100 (nps>0)
            sp = 0.0 if sec_id == 'sec-1' else 100.0
            enriched = enrich_fifo_lots(transactions, sell_date, sp, TAX_RATE)
            if not enriched.empty:
                all_enriched.append(enriched)

        combined = pd.concat(all_enriched)

        # Only sec-2 has nps > 0
        result = MinTaxStrategy(500.0).select_lots(combined)
        sec_ids = result.reset_index()['securityId'].unique().tolist()
        assert sec_ids == ['sec-2']

    def test_empty_lots_raises(self) -> None:
        with pytest.raises(InputError, match="No lots available"):
            MinTaxStrategy(1000.0).select_lots(TaxLotSellSchema.empty())


# --- AllocationPreservingStrategy (security-level, no taxonomy) ---

class TestAllocationPreservingStrategy:
    def test_sells_every_holding(self) -> None:
        """Even a small target must touch every security, never just one."""
        portfolio = _make_portfolio(
            transactions_data=[
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 9000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR']],
        )
        enriched = _enrich_multi(portfolio, sell_price=100.0)

        result = AllocationPreservingStrategy(500.0).select_lots(enriched)
        sec_ids = set(result.reset_index()['securityId'].unique())
        assert sec_ids == {'sec-1', 'sec-2'}

    def test_preserves_allocation(self) -> None:
        """Two equally-valued securities must be sold in equal share amounts."""
        portfolio = _make_portfolio(
            transactions_data=[
                # sec-1 high gain, sec-2 low gain, but identical current value (100 shares @ 100)
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 9000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR']],
        )
        enriched = _enrich_multi(portfolio, sell_price=100.0)

        result = AllocationPreservingStrategy(5000.0).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        shares_by_security = result.reset_index().groupby('securityId')['shares'].sum()
        assert shares_by_security['sec-1'] == pytest.approx(shares_by_security['sec-2'])

    def test_preserves_allocation_with_unequal_weights(self) -> None:
        """Selling must shrink every holding by the same fraction, keeping weights intact."""
        portfolio = _make_portfolio(
            transactions_data=[
                # sec-1 current value 30000 (300 @ 100), sec-2 current value 10000 (100 @ 100)
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 15000.0, 300.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 8000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR']],
        )
        enriched = _enrich_multi(portfolio, sell_price=100.0)

        result = AllocationPreservingStrategy(5000.0).select_lots(enriched)
        shares_by_security = result.reset_index().groupby('securityId')['shares'].sum()

        # sec-1 holds 3x the shares of sec-2, so it must sell 3x as many
        assert shares_by_security['sec-1'] == pytest.approx(3 * shares_by_security['sec-2'])

    def test_hits_target_net(self) -> None:
        portfolio = _make_portfolio(
            transactions_data=[
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 9000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR']],
        )
        enriched = _enrich_multi(portfolio, sell_price=100.0)

        result = AllocationPreservingStrategy(4000.0).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        assert result['netProceeds'].sum() == pytest.approx(4000.0, abs=0.5)

    def test_respects_fifo_within_security(self) -> None:
        """Within a security the oldest lot is consumed first."""
        portfolio = _make_portfolio(
            transactions_data=[
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 900.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2021, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 500.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
        )
        enriched = _enrich(portfolio, sell_price=100.0)

        # A target above the first lot's net must fully consume lot 1 before spilling into lot 2
        full_net = enriched['netProceeds'].sum()
        result = AllocationPreservingStrategy(full_net * 0.75).select_lots(enriched)
        result = result.reset_index().sort_values('date')

        assert len(result) == 2
        assert result.iloc[0]['shares'] == pytest.approx(10.0)
        assert 0.0 < result.iloc[1]['shares'] < 10.0

    def test_target_exceeds_max_raises(self) -> None:
        portfolio = _make_portfolio([
            [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 10000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        ])
        enriched = _enrich(portfolio, sell_price=150.0)

        with pytest.raises(InputError, match="exceeds maximum achievable"):
            AllocationPreservingStrategy(999999.0).select_lots(enriched)

    def test_empty_lots_raises(self) -> None:
        with pytest.raises(InputError, match="No lots available"):
            AllocationPreservingStrategy(1000.0).select_lots(TaxLotSellSchema.empty())


# --- AllocationPreservingStrategy (category-level, with taxonomy) ---

class TestAllocationPreservingStrategyByCategory:
    # Equity class: sec-1 (high gain, high tax) + sec-2 (low gain, low tax), each worth 10000.
    # Bonds class: sec-3 (low gain), worth 10000.
    CATEGORY_MAP = {'sec-1': 'Equity', 'sec-2': 'Equity', 'sec-3': 'Bonds'}

    def _portfolio(self) -> Portfolio:
        return _make_portfolio(
            transactions_data=[
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 9500.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-3', TransactionType.BUY.value, 9800.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR'], ['Bond C', 'WKN3', 'EUR']],
        )

    def test_consolidates_within_class(self) -> None:
        """A redundant high-tax security in a class is left untouched when the class quota fits the cheaper one."""
        enriched = _enrich_multi(self._portfolio(), sell_price=100.0)

        result = AllocationPreservingStrategy(8000.0, self.CATEGORY_MAP).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        sec_ids = set(result.reset_index()['securityId'].unique())
        assert sec_ids == {'sec-2', 'sec-3'}  # sec-1 (high tax) consolidated away

    def test_preserves_class_weights(self) -> None:
        """Each class must lose the same fraction of its value, regardless of which security supplies it."""
        enriched = _enrich_multi(self._portfolio(), sell_price=100.0)

        result = AllocationPreservingStrategy(8000.0, self.CATEGORY_MAP).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        sold = result.reset_index()
        sold['cls'] = sold['securityId'].map(self.CATEGORY_MAP)
        gross_by_class = sold.groupby('cls')['grossProceeds'].sum()
        # Equity class is worth 20000, Bonds class 10000
        assert gross_by_class['Equity'] / 20000 == pytest.approx(gross_by_class['Bonds'] / 10000, abs=1e-3)

    def test_spills_within_class_when_quota_exceeds_cheapest(self) -> None:
        """When a class must give up more than its cheapest security holds, the sale spills into the next."""
        enriched = _enrich_multi(self._portfolio(), sell_price=100.0)

        result = AllocationPreservingStrategy(20000.0, self.CATEGORY_MAP).select_lots(enriched)
        result = finalize_sell_lots(result, TAX_RATE)

        shares_by_security = result.reset_index().groupby('securityId')['shares'].sum()
        assert shares_by_security['sec-2'] == pytest.approx(100.0)  # cheaper equity fully consumed first
        assert 0.0 < shares_by_security['sec-1'] < 100.0            # then spills into the pricier one


# --- AllocationPreservingStrategy (--min-amount) ---

class TestAllocationPreservingStrategyMinAmount:
    # Equity 20000 (sec-1+sec-2), Bonds 10000 (sec-3), Cash 500 (sec-4, tiny)
    CATEGORY_MAP = {'sec-1': 'Equity', 'sec-2': 'Equity', 'sec-3': 'Bonds', 'sec-4': 'Cash'}

    def _enriched(self) -> DataFrame[TaxLotSellSchema]:
        portfolio = _make_portfolio(
            transactions_data=[
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 5000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 9500.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-3', TransactionType.BUY.value, 9800.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-4', TransactionType.BUY.value, 490.0, 5.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR'], ['Bond C', 'WKN3', 'EUR'], ['Cash D', 'WKN4', 'EUR']],
        )
        return _enrich_multi(portfolio, sell_price=100.0)

    def test_skips_small_class_and_still_hits_target(self) -> None:
        enriched = self._enriched()

        strategy = AllocationPreservingStrategy(5000.0, self.CATEGORY_MAP, min_amount=200.0)
        result = finalize_sell_lots(strategy.select_lots(enriched), TAX_RATE)

        sec_ids = set(result.reset_index()['securityId'].unique())
        assert 'sec-4' not in sec_ids  # tiny Cash class (quota well below 200) is left unsold
        assert strategy.excluded_groups == ['Cash']  # and it is reported to the caller
        assert result['netProceeds'].sum() == pytest.approx(5000.0, abs=1.0)  # target still met by the rest

    def test_cascade_excludes_multiple_classes(self) -> None:
        # Equity 100000 (sec-1), Bonds 1000 (sec-2), Cash 900 (sec-3): a low target keeps both
        # small classes' quotas under the minimum even after excluding one, so the loop excludes both.
        portfolio = _make_portfolio(
            transactions_data=[
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 50000.0, 1000.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 950.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-3', TransactionType.BUY.value, 882.0, 9.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['Bond B', 'WKN2', 'EUR'], ['Cash C', 'WKN3', 'EUR']],
        )
        enriched = _enrich_multi(portfolio, sell_price=100.0)

        strategy = AllocationPreservingStrategy(2000.0, {'sec-1': 'Equity', 'sec-2': 'Bonds', 'sec-3': 'Cash'}, min_amount=500.0)
        result = finalize_sell_lots(strategy.select_lots(enriched), TAX_RATE)

        assert strategy.excluded_groups == ['Cash', 'Bonds']  # smallest first, across two loop iterations
        assert set(result.reset_index()['securityId'].unique()) == {'sec-1'}
        assert result['netProceeds'].sum() == pytest.approx(2000.0, abs=1.0)

    def test_no_exclusion_when_all_classes_clear_the_minimum(self) -> None:
        enriched = self._enriched()

        strategy = AllocationPreservingStrategy(5000.0, self.CATEGORY_MAP, min_amount=10.0)
        result = strategy.select_lots(enriched)

        sec_ids = set(result.reset_index()['securityId'].unique())
        assert 'sec-4' in sec_ids  # Cash quota clears a 10.0 minimum, so it is still sold
        assert strategy.excluded_groups == []

    def test_infeasible_target_raises(self) -> None:
        enriched = self._enriched()
        max_net = (enriched['shares'] * enriched['netProceedsPerShare']).sum()

        # reaching (almost) the maximum needs the Cash class, but a 1000 minimum excludes it
        with pytest.raises(InputError, match="requires selling amounts below"):
            AllocationPreservingStrategy(max_net - 10.0, self.CATEGORY_MAP, min_amount=1000.0).select_lots(enriched)


class TestAllocationPreservingStrategyPerOrderFloor:
    # Equity = sec-1 (big, low tax) + sec-2 (tiny, 300 gross); Bonds = sec-3 (big).
    CATEGORY_MAP = {'sec-1': 'Equity', 'sec-2': 'Equity', 'sec-3': 'Bonds'}

    def _enriched(self) -> DataFrame[TaxLotSellSchema]:
        portfolio = _make_portfolio(
            transactions_data=[
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 9000.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 150.0, 3.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-3', TransactionType.BUY.value, 9800.0, 100.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR'], ['Bond C', 'WKN3', 'EUR']],
        )
        return _enrich_multi(portfolio, sell_price=100.0)  # sec-2 holds only 300 gross

    @staticmethod
    def _gross_per_order(result: DataFrame[TaxLotSellSchema]) -> pd.Series:
        sold = finalize_sell_lots(result, TAX_RATE).reset_index()
        return sold.groupby(['accountId', 'securityId'])['grossProceeds'].sum()

    def test_no_order_falls_below_the_minimum(self) -> None:
        enriched = self._enriched()

        result = AllocationPreservingStrategy(4000.0, self.CATEGORY_MAP, min_amount=1000.0).select_lots(enriched)

        assert (self._gross_per_order(result) >= 1000.0 - 0.01).all()  # every placed order clears the floor

    def test_tiny_holding_left_unsold_but_class_weight_preserved(self) -> None:
        enriched = self._enriched()

        strategy = AllocationPreservingStrategy(4000.0, self.CATEGORY_MAP, min_amount=1000.0)
        result = finalize_sell_lots(strategy.select_lots(enriched), TAX_RATE)

        sold = result.reset_index()
        assert 'sec-2' not in set(sold['securityId'])  # tiny holding (300 < 1000) never forms an order
        sold['cls'] = sold['securityId'].map(self.CATEGORY_MAP)
        gross_by_class = sold.groupby('cls')['grossProceeds'].sum()
        # Equity is worth 10300 (10000 + 300), Bonds 10000; each still sheds the same fraction
        assert gross_by_class['Equity'] / 10300 == pytest.approx(gross_by_class['Bonds'] / 10000, abs=1e-3)

    def test_donor_shift_keeps_both_orders_above_floor(self) -> None:
        """When a class needs two securities and the marginal would be a sliver, the split still clears the floor."""
        portfolio = _make_portfolio(
            transactions_data=[
                # Equity spread over two mid-sized securities so the class quota must use both
                [datetime(2020, 1, 1), 'acc-1', 'sec-1', TransactionType.BUY.value, 1900.0, 20.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
                [datetime(2020, 1, 1), 'acc-1', 'sec-2', TransactionType.BUY.value, 580.0, 6.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            ],
            securities_data=[['ETF A', 'WKN1', 'EUR'], ['ETF B', 'WKN2', 'EUR']],
        )
        enriched = _enrich_multi(portfolio, sell_price=100.0)  # caps: sec-1 2000, sec-2 600

        # single Equity class, target forces the quota above sec-1's 2000 cap -> sec-2 is the marginal,
        # whose naive slice (~28) is below 500; the donor shift must lift it rather than drop it
        strategy = AllocationPreservingStrategy(2000.0, {'sec-1': 'Equity', 'sec-2': 'Equity'}, min_amount=500.0)
        result = finalize_sell_lots(strategy.select_lots(enriched), TAX_RATE)

        gross = result.reset_index().groupby('securityId')['grossProceeds'].sum()
        assert set(gross.index) == {'sec-1', 'sec-2'}       # marginal kept as a valid order, not dropped
        assert (gross >= 500.0 - 0.01).all()                # both orders clear the floor
        assert result['netProceeds'].sum() == pytest.approx(2000.0, abs=1.0)  # target still met exactly

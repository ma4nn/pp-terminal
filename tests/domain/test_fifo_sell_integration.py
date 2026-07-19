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
from pathlib import Path
import tempfile

import pandas as pd
import pytest
from pp_terminal.data.filters import filter_by_account_and_security
from pp_terminal.domain.cost_basis import SellContext, calculate_fifo_sell

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import TransactionSchema
from pp_terminal.data.tax import calculate_prepaid_tax_per_lot, load_prepaid_tax_data_from_csv
from pp_terminal.data.pp_portfolio_builder import PpPortfolioBuilder
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from tests.conftest import TAX_RATE


@pytest.fixture(name='partial_sell_portfolio')
def provide_partial_sell_portfolio(request: pytest.FixtureRequest) -> Portfolio:
    """Load the partial_sell fixture with realistic buy/sell transactions."""
    fixture_path = request.path.parent.parent / 'fixtures' / 'partial_sell.ids.xml'
    return PpPortfolioBuilder().construct(fixture_path)


def test_partial_sell_remaining_shares(partial_sell_portfolio: Portfolio) -> None:
    """Test selling remaining shares after a partial sell."""
    snapshot = PortfolioSnapshot(partial_sell_portfolio, datetime(2024, 12, 31))

    # After selling 40 shares, 60 shares remain at 60€ each
    transactions = snapshot.securities_account_transactions.pipe(filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')
    lots = calculate_fifo_sell(transactions, SellContext(snapshot.date, 60.0, TAX_RATE), shares_to_sell=60.0)

    # All remaining 60 shares come from the original purchase at 5€
    assert len(lots) == 1
    assert lots.iloc[0]['shares'] == pytest.approx(60.0)
    assert lots.iloc[0]['purchasePrice'] == pytest.approx(5.0)
    assert lots.iloc[0]['costBasis'] == pytest.approx(300.0)
    assert lots.iloc[0]['capitalGain'] == pytest.approx(3300.0)  # 60 * (60 - 5)


def test_sell_on_purchase_date(partial_sell_portfolio: Portfolio) -> None:
    """Test selling shares on the same day as purchase."""
    snapshot = PortfolioSnapshot(partial_sell_portfolio, datetime(2023, 12, 30))
    transactions = snapshot.securities_account_transactions.pipe(filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')

    # Sell 10 shares on the same day they were purchased at 5€
    lots = calculate_fifo_sell(transactions, SellContext(snapshot.date, 5.0, TAX_RATE), shares_to_sell=10.0)

    assert len(lots) == 1
    assert lots.iloc[0]['shares'] == pytest.approx(10.0)
    assert lots.iloc[0]['purchasePrice'] == pytest.approx(5.0)
    assert lots.iloc[0]['capitalGain'] == pytest.approx(0.0)  # No gain when selling at purchase price


def test_capital_loss_scenario(partial_sell_portfolio: Portfolio) -> None:
    """Test selling at a loss (price below purchase price)."""
    snapshot = PortfolioSnapshot(partial_sell_portfolio, datetime(2024, 12, 31))
    transactions = snapshot.securities_account_transactions.pipe(filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')

    # Sell at 4€, below purchase price of 5€
    lots = calculate_fifo_sell(transactions, SellContext(snapshot.date, 4.0, TAX_RATE), shares_to_sell=20.0)

    assert len(lots) == 1
    assert lots.iloc[0]['shares'] == pytest.approx(20.0)
    assert lots.iloc[0]['purchasePrice'] == pytest.approx(5.0)
    assert lots.iloc[0]['capitalGain'] == pytest.approx(-20.0)  # 20 * (4 - 5)
    assert lots.iloc[0]['totalTax'] == pytest.approx(0.0)  # losses are never taxed
    assert lots.iloc[0]['netProceeds'] == pytest.approx(lots.iloc[0]['grossProceeds'])


def test_empty_transactions() -> None:
    df = calculate_fifo_sell(TransactionSchema.empty(), SellContext(datetime.now(), 60.0, TAX_RATE), shares_to_sell=10.0)

    assert df.empty


def test_zero_price_error(partial_sell_portfolio: Portfolio) -> None:
    """Test handling of zero sale price."""
    snapshot = PortfolioSnapshot(partial_sell_portfolio, datetime(2024, 12, 31))
    transactions = snapshot.securities_account_transactions.pipe(
        filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')

    # Zero price should work but result in negative capital gain
    lots = calculate_fifo_sell(transactions, SellContext(snapshot.date, 0.0, TAX_RATE), shares_to_sell=10.0)

    assert lots.iloc[0]['capitalGain'] == pytest.approx(-50.0)  # 10 * (0 - 5)
    assert lots.iloc[0]['totalTax'] == pytest.approx(0.0)


def test_very_small_shares(partial_sell_portfolio: Portfolio) -> None:
    """Test selling fractional shares."""
    snapshot = PortfolioSnapshot(partial_sell_portfolio, datetime(2024, 12, 31))
    transactions = snapshot.securities_account_transactions.pipe(
        filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')

    # Sell 0.5 shares
    lots = calculate_fifo_sell(transactions, SellContext(snapshot.date, 60.0, TAX_RATE), shares_to_sell=0.5)

    assert len(lots) == 1
    assert lots.iloc[0]['shares'] == pytest.approx(0.5)
    assert lots.iloc[0]['costBasis'] == pytest.approx(2.5)  # 0.5 * 5
    assert lots.iloc[0]['capitalGain'] == pytest.approx(27.5)  # 0.5 * (60 - 5)


def test_snapshot_at_different_dates(partial_sell_portfolio: Portfolio) -> None:
    """Test snapshots at different dates show correct holdings."""

    # Before any purchases
    snapshot_before = PortfolioSnapshot(partial_sell_portfolio, datetime(2023, 12, 29))
    transactions = snapshot_before.securities_account_transactions.pipe(
        filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')
    lots = calculate_fifo_sell(transactions, SellContext(snapshot_before.date, 50.0, TAX_RATE), shares_to_sell=10.0)
    assert lots.empty

    # After purchase, before sell - should have 100 shares
    snapshot_mid = PortfolioSnapshot(partial_sell_portfolio, datetime(2024, 1, 1))
    transactions = snapshot_mid.securities_account_transactions.pipe(
        filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')
    lots = calculate_fifo_sell(transactions, SellContext(snapshot_mid.date, 50.0, TAX_RATE), shares_to_sell=100.0)
    assert lots['shares'].sum() == pytest.approx(100.0)

    # After sell - should have 60 shares
    snapshot_after = PortfolioSnapshot(partial_sell_portfolio, datetime(2024, 12, 31))
    transactions = snapshot_after.securities_account_transactions.pipe(
        filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')
    lots = calculate_fifo_sell(transactions, SellContext(snapshot_after.date, 60.0, TAX_RATE), shares_to_sell=60.0)
    assert lots['shares'].sum() == pytest.approx(60.0)


def test_vorabpauschale_csv_calculation(partial_sell_portfolio: Portfolio) -> None:
    """Test Vorabpauschale credit calculation from CSV with month proration."""
    # Create test CSV data
    csv_data = pd.DataFrame({
        'year': [2023, 2024],
        'account_id': ['test-portfolio-uuid-001', 'test-portfolio-uuid-001'],
        'security_id': ['test-security-uuid-001', 'test-security-uuid-001'],
        'deemed_income': [0.379147, 0.758294],
    })
    csv_data = csv_data.set_index(['year', 'account_id', 'security_id'])

    # Purchase 100 shares on Dec 30, 2023, sell 50 shares on March 1, 2025
    snapshot = PortfolioSnapshot(partial_sell_portfolio, datetime(2025, 3, 1))
    transactions = partial_sell_portfolio.securities_account_transactions.pipe(
        filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')
    lots = calculate_fifo_sell(transactions, SellContext(snapshot.date, 60.0, TAX_RATE, tax_csv_data=csv_data), shares_to_sell=50.0)

    deemed_income = lots['deemedIncome'].sum()

    # Expected calculation:
    # Lot: 50 shares purchased 2023-12-30
    # Year 2023: 50 * 0.379147 * (13-12)/12 = 50 * 0.379147 * 1/12 = 1.58€  (purchased in December = 1 month)
    # Year 2024: 50 * 0.758294 * 1.0 = 37.91€  (full year)
    # Total: 39.49€
    assert abs(deemed_income - 39.4945) < 0.01


def test_vorabpauschale_csv_missing_data(partial_sell_portfolio: Portfolio) -> None:
    """Test Vorabpauschale credit when CSV has missing years (should use 0.0 silently)."""
    # Create test CSV with only 2024 data (missing 2023)
    csv_data = pd.DataFrame({
        'year': [2024],
        'account_id': ['test-portfolio-uuid-001'],
        'security_id': ['test-security-uuid-001'],
        'deemed_income': [0.758294],  # Equivalent to old tax_per_share 0.2
    })
    csv_data = csv_data.set_index(['year', 'account_id', 'security_id'])

    snapshot = PortfolioSnapshot(partial_sell_portfolio, datetime(2025, 3, 1))
    transactions = snapshot.securities_account_transactions.pipe(filter_by_account_and_security, account_id='test-portfolio-uuid-001', security_id='test-security-uuid-001')

    lots = calculate_fifo_sell(transactions, SellContext(snapshot.date, 60.0, TAX_RATE, tax_csv_data=csv_data), shares_to_sell=50.0)

    credit = float(calculate_prepaid_tax_per_lot(lots, datetime(2025, 3, 1), csv_data).sum())

    # Only 2024 has data: 50 shares * 0.758294 deemed_income * 1.0 month_factor = 37.9147
    # 2023 is missing, so 0.0 is used
    # Note: calculate_prepaid_tax_per_lot now returns deemed income base, not tax
    assert abs(credit - 37.9147) < 0.01


def test_exempt_rate_applied_to_total_capital_gain() -> None:
    """
    Test simplified tax formula where exemption rate is applied once to adjusted capital gain.

    German tax law (§18 InvStG): The exemption rate applies to all taxable gains from a security.
    The simplified tax calculation:
      adjustedGain = capitalGain - deemedIncome
      taxableGain = adjustedGain * (1 - exempt_rate)
      totalTax = taxableGain * tax_rate

    This avoids duplicate application of exemption rate (once in CSV loading, once in sell calculation).
    """
    exempt_rate = 30.0
    tax_rate = 26.375

    # Create portfolio with single security
    accounts = pd.DataFrame({
        'name': ['Test Account'],
        'type': ['portfolio'],
        'referenceAccount': [None],
        'isRetired': [False],
        'currency': ['EUR']
    }, index=['acc-1'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame({
        'name': ['Test ETF'],
        'wkn': ['ETF123'],
        'isin': ['TEST123456'],
        'currency': ['EUR'],
        'isRetired': [False],
        'note': [None]
    }, index=['sec-1'])
    securities.index.name = 'securityId'

    # Buy 100 shares at 100€ on 2020-01-01
    transactions = pd.DataFrame({
        'type': ['BUY'],
        'amount': [10000.0],
        'shares': [100.0],
        'accountType': ['portfolio'],
        'currency': ['EUR'],
        'taxes': [0.0],
        'fees': [10.0]  # 10€ fees
    }, index=pd.MultiIndex.from_tuples([
        (pd.Timestamp('2020-01-01'), 'acc-1', 'sec-1')
    ], names=['date', 'accountId', 'securityId']))

    portfolio = Portfolio(accounts, transactions, securities, pd.DataFrame())

    # CSV with VAP deemed income: deemed_income = 100€/year total, 1€ per share
    # Note: Exemption rate is now applied during sell calculation, not during CSV loading
    # This ensures consistent application of exemption to all gains
    csv_data = pd.DataFrame({
        'isin': ['TEST123456', 'TEST123456'],
        'year': [2020, 2021],
        'deemed_income': [1.0, 1.0],  # 100€ total / 100 shares
    })

    # Load CSV and apply exemption rate (simulating what happens with real portfolio)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        csv_data.to_csv(f.name, sep=';', index=False)
        csv_path = Path(f.name)

    csv_data = load_prepaid_tax_data_from_csv(csv_path, portfolio)

    # Sell 100 shares at 150€ on 2022-06-01
    # Capital gain = 150*100 - (100*100 + 10) = 15000 - 10010 = 4990€
    snapshot = PortfolioSnapshot(portfolio, datetime(2022, 6, 1))
    transactions = snapshot.securities_account_transactions.pipe(
        filter_by_account_and_security,
        account_id='acc-1',
        security_id='sec-1'
    )

    lots = calculate_fifo_sell(
        transactions,
        SellContext(datetime(2022, 6, 1), 150.0, tax_rate, exempt_rate=exempt_rate, tax_csv_data=csv_data),
        shares_to_sell=100.0,
    )

    # Expected calculation with simplified formula:
    # Capital gain: 4990€
    # Prepaid deemed income base: 100 shares * 1€ * 2 years = 200€
    # Adjusted gain: 4990 - 200 = 4790€
    # Taxable gain (after 30% exemption): 4790 * 0.7 = 3353€
    # Total tax: 3353 * 0.26375 = 884.35€

    assert len(lots) == 1
    lot = lots.iloc[0]

    # Verify core calculations with new simplified formula
    assert abs(lot['capitalGain'] - 4990.0) < 0.01, f"Capital gain should be 4990, got {lot['capitalGain']}"
    assert abs(lot['deemedIncome'] - 200.0) < 0.01, f"Prepaid deemed income base should be 200, got {lot['deemedIncome']}"
    # New formula: taxableGain = (capitalGain - deemedIncome) * (1 - exemption)
    # = (4990 - 200) * 0.7 = 4790 * 0.7 = 3353
    assert abs(lot['taxableGain'] - 3353.0) < 0.01, f"Taxable gain should be 3353 ((4990-200) * 0.7), got {lot['taxableGain']}"
    assert abs(lot['totalTax'] - 884.35375) < 0.01, f"Total tax should be ~884.35, got {lot['totalTax']}"

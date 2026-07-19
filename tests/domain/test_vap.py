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

import pandas as pd
from _pytest.fixtures import TopRequest
from pandas.testing import assert_frame_equal
from pandera.typing import DataFrame
import pytest

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import TransactionType, AccountType, Percent, Money, VapResultSchema, SecuritySchema
from pp_terminal.domain.vap import calculate_vap, apply_allowance_to_vap
from pp_terminal.data.pp_portfolio_builder import PpPortfolioBuilder
from tests.conftest import TAX_RATE, EXEMPT_RATE_CONFIG


@pytest.fixture(name='sample_securities')
def provide_sample_securities() -> DataFrame[SecuritySchema]:
    securities = pd.DataFrame([['Some Share', 'A23432', 'EUR']], columns=['name', 'wkn', 'currency'], index=['1234567890'])
    securities.index.name = 'securityId'

    return SecuritySchema.validate(securities)


@pytest.fixture(name='sample_prices')
def provide_sample_prices() -> pd.DataFrame:
    return (pd.DataFrame([
        [datetime(2017, 12, 30), '1234567890', 200.0],
        [datetime(2018, 1, 10), '1234567890', 246.66],
    ], columns=['date', 'securityId', 'price'])
            .set_index(['date', 'securityId']))


def test_calculate_empty_if_no_securities_accounts(sample_accounts: pd.DataFrame, sample_securities: pd.DataFrame, sample_prices: pd.DataFrame) -> None:
    transactions = (pd.DataFrame([
        [datetime(2018, 8, 15), TransactionType.BUY.value, 1000.0, 5.0, '1234567890', '1', AccountType.SECURITIES.value, 'EUR']
    ], columns=['date', 'type', 'amount', 'shares', 'securityId', 'accountId', 'accountType', 'currency'])
                    .set_index(['date', 'accountId', 'securityId']))

    # drop all rows but keep structure
    sample_accounts = sample_accounts.drop(sample_accounts.index)
    sample_securities = sample_securities.drop(sample_securities.index)
    sample_prices = sample_prices.drop(sample_prices.index)
    transactions = transactions.drop(transactions.index)

    portfolio = Portfolio(sample_accounts, transactions, sample_securities, sample_prices)
    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2022, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2022, 12, 31))

    result = calculate_vap(snapshot_begin, snapshot_end, 2.29, TAX_RATE)

    assert result.empty


def test_calculate_empty_if_no_security_prices(sample_accounts: pd.DataFrame, sample_transactions: pd.DataFrame, sample_securities: pd.DataFrame, sample_prices: pd.DataFrame) -> None:
    sample_prices = sample_prices.drop(sample_prices.index)
    sample_transactions = sample_transactions.drop(sample_transactions.index)

    portfolio = Portfolio(sample_accounts, sample_transactions, sample_securities, sample_prices)

    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2022, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2022, 12, 31))

    result = calculate_vap(snapshot_begin, snapshot_end, 2.29, TAX_RATE)

    assert result.empty


def test_inyear_buy(sample_accounts: pd.DataFrame, sample_transactions: pd.DataFrame, sample_securities: pd.DataFrame, sample_prices: pd.DataFrame) -> None:
    portfolio = Portfolio(sample_accounts, sample_transactions, sample_securities, sample_prices)
    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2018, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2018, 12, 31))

    expected_df = DataFrame[VapResultSchema]([['A23432', 'Some Share', 'EUR', 1.76]], columns=['wkn', 'name', 'currency', 'Testdepot'], index=['1234567890'])
    expected_df.index.name = 'securityId'
    expected_df = VapResultSchema.validate(expected_df)

    result = calculate_vap(snapshot_begin, snapshot_end, 2.29, TAX_RATE, exempt_rate_percent=0.0)

    assert not result.empty
    assert_frame_equal(expected_df, result.round(2))

# @see https://github.com/MStrecke/vorabpauschale/blob/master/test.ini
# @see https://www.justetf.com/de/news/etf/etf-und-steuern-das-neue-investmentsteuergesetz-ab-2018.html
samples = [
    (0.0, 0, 10_000, 10_300, 0, TAX_RATE),  # zero base rate
    (0.0, 0, 10_000, 10_300, -1.29, TAX_RATE),  # negative base rate
    (295.95, 0, 100_000, 125_000, 2.29, TAX_RATE),
    (0.0, 300, 10_000, 9750, 2.53, TAX_RATE),  # justetf Steuer-Beispiel 1.1: Ausschüttender ETF mit kleinem Gewinn
    (0.0, 0, 10_000, 9750, 2.53, TAX_RATE),  # justetf Steuer-Beispiel 1.1 mit Verlust ohne Ausschüttung
    (9.23, 0, 10_000, 10_050, 2.53, TAX_RATE),  # justetf Steuer-Beispiel 1.2: Thesaurierender ETF mit kleinem Gewinn
    (0.0, 300, 10_000, 10_700, 2.53, TAX_RATE),  # justetf Steuer-Beispiel 2.1: Ausschüttender ETF mit hohem Gewinn
    (32.70, 0, 10_000, 11_000, 2.53, TAX_RATE),  # justetf Steuer-Beispiel 2.2: Thesaurierender ETF mit hohem Gewinn
    (14.22, 0, 10_000, 10_700, 1.1, TAX_RATE),
    (1.18, 0.1, 100, 102, 2.55, 100),  # https://www.consorsbank.de/web/Wissen/FAQ/steuer/Berechnung-Vorabpauschale
    (234.66, 500, 100_000, 110_000, 2.53, TAX_RATE),  # https://www.smart-rechner.de/vorabpauschale/beispiel.php
    (18.46, 0, 10_000, 10_100, 2.53, TAX_RATE),  # https://www.umweltbank.de/magazin/finanzwissen/investieren/vorabpauschale/
]


@pytest.mark.parametrize("expected_tax_value, payout, value_begin, value_end, base_rate_percent, tax_rate_percent", samples)
def test_single_security_buy_only(   # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        sample_accounts: pd.DataFrame,
        sample_securities: pd.DataFrame,
        expected_tax_value: Money,
        payout: Money,
        value_begin: Money,
        value_end: Money,
        base_rate_percent: Percent,
        tax_rate_percent: Percent
) -> None:
    share_price_begin = 50
    shares = value_begin/share_price_begin

    prices = pd.DataFrame([
        [datetime(2023, 12, 1), '1234567890', 46.54],
        [datetime(2023, 12, 5), '1234567890', share_price_begin],
        [datetime(2024, 2, 1), '1234567890', 52.01],
        [datetime(2024, 6, 1), '1234567890', 60.4222],
        [datetime(2024, 12, 31), '1234567890', value_end / shares],
        [datetime(2023, 12, 1), '1234567890', 46.54],
        [datetime(2025, 1, 2), '1234567890', 45.302],
    ], columns=['date', 'securityId', 'price']).set_index(['date', 'securityId'])
    transactions = pd.DataFrame([
        [datetime(2023, 12, 6), TransactionType.BUY.value, float(value_begin), shares, '1234567890', '1', AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2024, 6, 4), TransactionType.DIVIDENDS.value, float(payout), shares, '1234567890', '1', AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'type', 'amount', 'shares', 'securityId', 'accountId', 'accountType', 'currency', 'taxes', 'fees']).set_index(['date', 'accountId', 'securityId'])

    portfolio = Portfolio(sample_accounts, transactions, sample_securities, prices)

    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2024, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2024, 12, 31))

    expected_df = DataFrame[VapResultSchema]([['A23432', 'Some Share', 'EUR', expected_tax_value]], columns=['wkn', 'name', 'currency', 'Testdepot'], index=['1234567890'])
    expected_df.index.name = 'securityId'
    expected_df = VapResultSchema.validate(expected_df)

    result = calculate_vap(
        snapshot_begin,
        snapshot_end,
        base_rate_percent=base_rate_percent,
        tax_rate_percent=tax_rate_percent,
        exempt_rate_percent=30)

    if expected_tax_value == 0:
        assert result.empty
    else:
        assert not result.empty
        assert_frame_equal(expected_df, round(result, 2))


def test_kommer_2021(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')
    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2021, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2021, 12, 31))

    expected_df = DataFrame[VapResultSchema]([
        ['ETF013', 'Lyxor MSCI Pacific UCITS ETF', 'EUR', 2.064846],
        ['A0MZWQ', 'iShares Core MSCI Europe UCITS ETF EUR (Dist)', 'EUR', 5.549975],
        ['A2DK6R', 'iShares Diversified Commodity Swap UCITS ETF', 'EUR', 4.821648],
        ['A0HGWC', 'iShares MSCI EM UCITS ETF (Dist)', 'EUR', 9.614679],
        ['A0J201', 'iShares MSCI North America UCITS ETF', 'EUR', 8.346675]
    ], columns=['wkn', 'name', 'currency', 'Depot'], index=[
        'ff0a2b77-9749-45b0-8333-cb1d9787812c',
        'c770a389-0a84-442c-ad85-2a58c3066924',
        '97000a3b-0a3d-4779-ad6c-1234bfea5e72',
        '47094920-535c-4508-9a92-80c01933f567',
        'daab10fd-c3fb-4430-a368-0ce0cdf551c8'
    ])
    expected_df.index.name = 'securityId'
    expected_df['Depot'] *= 0.7  # respect exemption rate
    expected_df = VapResultSchema.validate(expected_df)

    result = calculate_vap(
        snapshot_begin,
        snapshot_end,
        base_rate_percent=2.0,
        tax_rate_percent=TAX_RATE,
        exempt_rate_percent=30,
        exempt_rate_attr_uuid="2baac2d0-459b-4b41-a0ef-d7dad0866892")

    assert_frame_equal(expected_df, result)


def test_kommer_2023(request: TopRequest) -> None:
    config = {
        "attributes": {
            "securities": {
                "exemption-rate": "2baac2d0-459b-4b41-a0ef-d7dad0866892"
            }
        }
    }
    portfolio = PpPortfolioBuilder(config=config).construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')
    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2023, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2023, 12, 31))

    expected_df = DataFrame[VapResultSchema]([
        ['ETF013', 'Lyxor MSCI Pacific UCITS ETF', 'EUR', 1.42471],
        ['A0RL83', 'iShares Core Euro Government Bond UCITS ETF (Dist)', 'EUR', 8.05472],
        ['A0MZWQ', 'iShares Core MSCI Europe UCITS ETF EUR (Dist)', 'EUR', 4.24526],
        ['A0HGWC', 'iShares MSCI EM UCITS ETF (Dist)', 'EUR', 5.75229],
        ['A0J201', 'iShares MSCI North America UCITS ETF', 'EUR', 6.83661]
    ], columns=['wkn', 'name', 'currency', 'Depot'], index=[
        'ff0a2b77-9749-45b0-8333-cb1d9787812c',
        '99b9419f-8c70-422e-8e8e-05eadb4507ec',
        'c770a389-0a84-442c-ad85-2a58c3066924',
        '47094920-535c-4508-9a92-80c01933f567',
        'daab10fd-c3fb-4430-a368-0ce0cdf551c8'
    ])
    expected_df.index.name = 'securityId'
    expected_df = VapResultSchema.validate(expected_df)

    result = calculate_vap(snapshot_begin, snapshot_end, 2.0, TAX_RATE, 30.0, config['attributes']['securities']['exemption-rate'])

    assert not result.empty
    assert_frame_equal(expected_df, result.round(5))


def test_empty_file(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'empty.ids.xml')

    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2021, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2021, 12, 31))

    result = calculate_vap(snapshot_begin, snapshot_end, 2.0, TAX_RATE)

    assert result.empty


def test_zero_base_rate(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2021, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2021, 12, 31))

    result = calculate_vap(snapshot_begin, snapshot_end, base_rate_percent=0, tax_rate_percent=TAX_RATE)

    assert result.empty


def test_zero_tax_rate(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2021, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2021, 12, 31))

    result = calculate_vap(snapshot_begin, snapshot_end, base_rate_percent=2.0, tax_rate_percent=0)

    assert result.empty


def test_full_exempt_rate(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2021, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2021, 12, 31))

    result = calculate_vap(snapshot_begin, snapshot_end, base_rate_percent=2.0, tax_rate_percent=TAX_RATE, exempt_rate_percent=100.0)

    assert result.empty


def test_custom_exempt_rate_produces_positive_vap(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder(config=EXEMPT_RATE_CONFIG).construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

    snapshot_begin = PortfolioSnapshot(portfolio, datetime(2023, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(2023, 12, 31))

    result = calculate_vap(
        snapshot_begin,
        snapshot_end,
        base_rate_percent=2.0,
        tax_rate_percent=TAX_RATE,
        exempt_rate_percent=30.0,
        exempt_rate_attr_uuid=EXEMPT_RATE_CONFIG['attributes']['securities']['exempt-rate']
    )

    assert not result.empty

    vap_values = result[result['name'] != 'Related Account Balance']
    account_columns = [col for col in vap_values.columns if col not in ['wkn', 'name', 'currency']]

    for col in account_columns:
        for idx, value in vap_values[col].items():
            if pd.notna(value):
                assert value >= 0, f"VAP should be non-negative, got {value} for {vap_values.loc[idx, 'name']} in {col}"


def _vap_table() -> DataFrame[VapResultSchema]:
    """Two securities in one account, VAP tax 100 and 300 (grand total 400)."""
    frame = pd.DataFrame({
        'wkn': ['W1', 'W2'], 'name': ['ETF A', 'ETF B'], 'currency': ['EUR', 'EUR'], 'Depot': [100.0, 300.0],
    })
    return VapResultSchema.validate(frame)


def test_apply_allowance_to_vap_reduces_total_by_allowance_times_rate() -> None:
    relieved = apply_allowance_to_vap(_vap_table(), allowance=1000.0, tax_rate=26.375)

    # total VAP tax 400 - 1000*0.26375 (263.75) = 136.25, split proportionally 1:3
    assert relieved['Depot'].sum() == pytest.approx(400.0 - 263.75)
    assert relieved.loc[0, 'Depot'] == pytest.approx(relieved.loc[1, 'Depot'] / 3)


def test_apply_allowance_to_vap_never_goes_negative() -> None:
    relieved = apply_allowance_to_vap(_vap_table(), allowance=1_000_000.0, tax_rate=26.375)

    assert relieved['Depot'].sum() == pytest.approx(0.0)  # allowance dwarfs the VAP -> no tax owed


def test_apply_allowance_to_vap_zero_is_noop() -> None:
    relieved = apply_allowance_to_vap(_vap_table(), allowance=0.0, tax_rate=26.375)

    assert relieved['Depot'].sum() == pytest.approx(400.0)

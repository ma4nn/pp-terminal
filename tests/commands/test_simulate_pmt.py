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
import logging

import pandas as pd
import pytest

from pp_terminal.commands.simulate_pmt import amortization_factor, blended_return_from_allocation, prepare_pmt_result, split_return_scenario, _format_value, _next_step_hint, _resolve_return_scenarios
from pp_terminal.utils.config import empty_config
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import AccountType, Money, Percent, TransactionType
from pp_terminal.exceptions import InputError


_DATE = datetime(2025, 1, 1)
_END_DATE = datetime(2055, 1, 1)
_HORIZON_YEARS = (_END_DATE - _DATE).days / 365.25
_TAX_RATE = 26.375


def _deposit_account(amount: float, retired: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    accounts = pd.DataFrame([
        ['Cash Account', AccountType.DEPOSIT.value, None, retired, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'], index=['dep-1'])
    accounts.index.name = 'accountId'

    transaction_type = TransactionType.DEPOSIT.value if amount >= 0 else TransactionType.REMOVAL.value
    transactions = pd.DataFrame([
        [datetime(2020, 1, 1), 'dep-1', None, transaction_type, amount, 0.0, AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    return accounts, transactions


@pytest.fixture(name='portfolio_with_prices')
def provide_portfolio_with_prices(portfolio_with_purchases: Portfolio) -> Portfolio:
    """45 shares of sec-1 with a cost basis of 4500, priced at 200 -> market value 9000, capital gain 4500."""
    prices = pd.DataFrame([
        [datetime(2024, 12, 31), 'sec-1', 200.0],
    ], columns=['date', 'securityId', 'price']).set_index(['date', 'securityId'])

    return Portfolio(
        accounts=portfolio_with_purchases.securities_accounts,
        transactions=portfolio_with_purchases.securities_account_transactions,
        securities=portfolio_with_purchases.securities,
        prices=prices,
    )


@pytest.fixture(name='cash_only_portfolio')
def provide_cash_only_portfolio() -> Portfolio:
    accounts, transactions = _deposit_account(100000.0)
    return Portfolio(accounts=accounts, transactions=transactions)


def test_amortization_factor_zero_rate_is_linear() -> None:
    assert amortization_factor(0, 30) == pytest.approx(1 / 30)
    assert amortization_factor(0, 1) == pytest.approx(1.0)


def test_amortization_factor_known_annuity_value() -> None:
    assert amortization_factor(0.05, 30) == pytest.approx(0.0619537, abs=1e-6)


def test_amortization_factor_final_year_withdraws_everything() -> None:
    assert amortization_factor(0.05, 0.5) == 1.0
    assert amortization_factor(0.0, 1.0) == 1.0


@pytest.mark.parametrize('rate', [0.0, 0.02, 0.05, 0.08])
def test_amortization_factor_depletes_capital_exactly(rate: float) -> None:
    years, balance = 30, 100000.0
    withdrawal = balance * amortization_factor(rate, years)
    for _ in range(years):
        balance = (balance - withdrawal) * (1 + rate)
    assert balance == pytest.approx(0, abs=1e-6)


def test_prepare_pmt_result_taxes_the_drawn_gain(portfolio_with_prices: Portfolio) -> None:
    result = prepare_pmt_result(portfolio_with_prices, empty_config(), _DATE, _TAX_RATE, [5.0], _END_DATE, allowance=0.0)

    row = result.iloc[0]
    gross = 9000.0 * amortization_factor(0.05, _HORIZON_YEARS)
    gain_per_euro = 4500.0 * (1 - 0.30) / 9000.0  # default 30% Teilfreistellung
    expected_tax = gross * gain_per_euro * _TAX_RATE / 100

    assert row['grossPerYear'] == pytest.approx(gross)
    assert row['grossRate'] == pytest.approx(row['grossPerYear'] / 9000.0 * 100)
    assert row['netPerYear'] == pytest.approx(gross - expected_tax)
    assert row['netPerYear'] < row['grossPerYear']
    assert row['netPerMonth'] == pytest.approx(row['netPerYear'] / 12)
    assert row['netRate'] == pytest.approx(row['netPerYear'] / 9000.0 * 100)
    assert row['netRate'] <= row['grossRate']


def test_prepare_pmt_result_allowance_shelters_small_gain(portfolio_with_prices: Portfolio) -> None:
    result = prepare_pmt_result(portfolio_with_prices, empty_config(), _DATE, _TAX_RATE, [5.0], _END_DATE, allowance=10000.0)

    row = result.iloc[0]
    assert row['netPerYear'] == pytest.approx(row['grossPerYear'])


def test_prepare_pmt_result_cash_only_is_untaxed(cash_only_portfolio: Portfolio) -> None:
    result = prepare_pmt_result(cash_only_portfolio, empty_config(), _DATE, _TAX_RATE, [5.0], _END_DATE)

    row = result.iloc[0]
    assert row['grossPerYear'] == pytest.approx(100000.0 * amortization_factor(0.05, _HORIZON_YEARS))
    assert row['netPerYear'] == pytest.approx(row['grossPerYear'])
    assert row['grossRate'] == pytest.approx(amortization_factor(0.05, _HORIZON_YEARS) * 100)
    assert row['netRate'] == pytest.approx(row['grossRate'])  # untaxed cash: net equals gross


def test_prepare_pmt_result_reports_the_start_capital_it_is_based_on(cash_only_portfolio: Portfolio) -> None:
    result = prepare_pmt_result(cash_only_portfolio, empty_config(), _DATE, _TAX_RATE, [5.0, 2.0], _END_DATE)

    # repeated per row, since csv and json output have no channel for anything but the table
    assert list(result['startCapital']) == pytest.approx([100000.0, 100000.0])
    # the rates the table shows are percentages of exactly this amount
    assert result.iloc[0]['grossPerYear'] == pytest.approx(result.iloc[0]['startCapital'] * result.iloc[0]['grossRate'] / 100)


def test_prepare_pmt_result_labels_amounts_with_the_base_currency(cash_only_portfolio: Portfolio) -> None:
    result = prepare_pmt_result(cash_only_portfolio, empty_config(), _DATE, _TAX_RATE, [5.0, 2.0], _END_DATE)

    assert list(result['currency']) == [cash_only_portfolio.base_currency] * 2


def test_percent_columns_are_not_formatted_as_money() -> None:
    """Money and Percent are both plain floats, so only the column name keeps the currency off the rates."""
    row = pd.Series({'currency': 'EUR', 'grossRate': Percent(5.73), 'grossPerYear': Money(1338.44)})

    assert '€' not in _format_value(row['grossRate'], 'grossRate', row)
    assert '€' in _format_value(row['grossPerYear'], 'grossPerYear', row)


def test_prepare_pmt_result_zero_return_spreads_capital_evenly(cash_only_portfolio: Portfolio) -> None:
    result = prepare_pmt_result(cash_only_portfolio, empty_config(), _DATE, _TAX_RATE, [0.0], _END_DATE)

    assert result.iloc[0]['grossPerYear'] == pytest.approx(100000.0 / _HORIZON_YEARS)


def test_prepare_pmt_result_one_row_per_return_rate(cash_only_portfolio: Portfolio) -> None:
    result = prepare_pmt_result(cash_only_portfolio, empty_config(), _DATE, _TAX_RATE, [2.0, 5.0, 0.0], _END_DATE)

    assert list(result['assumedReturn']) == [2.0, 5.0, 0.0]  # given order is kept
    for _, row in result.iterrows():
        assert row['grossPerYear'] == pytest.approx(100000.0 * amortization_factor(row['assumedReturn'] / 100, _HORIZON_YEARS))


def test_prepare_pmt_result_empty_portfolio() -> None:
    assert prepare_pmt_result(Portfolio(), empty_config(), _DATE, _TAX_RATE, [5.0], _END_DATE).empty


def test_prepare_pmt_result_no_rates() -> None:
    assert prepare_pmt_result(Portfolio(), empty_config(), _DATE, _TAX_RATE, [], _END_DATE).empty


def test_prepare_pmt_result_missing_prices(portfolio_with_purchases: Portfolio) -> None:
    with pytest.raises(InputError, match="No price data"):
        prepare_pmt_result(portfolio_with_purchases, empty_config(), _DATE, _TAX_RATE, [5.0], _END_DATE)


def test_prepare_pmt_result_end_date_must_be_in_the_future(cash_only_portfolio: Portfolio) -> None:
    with pytest.raises(InputError, match="end date must be after"):
        prepare_pmt_result(cash_only_portfolio, empty_config(), _DATE, _TAX_RATE, [5.0], datetime(2024, 1, 1))


def test_next_step_hint_splits_gross_proportionally_between_cash_and_securities() -> None:
    hint = _next_step_hint(1000.0, 200.0, 5.0)  # 5% withdrawal rate on 200 cash = 10 drawn from cash
    assert 'spend 10.00' in hint
    assert 'cash balance of 200.00' in hint
    assert '--target-gross 990.00' in hint


def test_next_step_hint_without_cash_targets_full_gross() -> None:
    for hint in (_next_step_hint(1000.0, 0.0, 5.0), _next_step_hint(1000.0, -500.0, 5.0)):
        assert '--target-gross 1000.00' in hint
        assert 'cash balance' not in hint


def test_next_step_hint_cash_heavy_funds_entirely_from_cash() -> None:
    hint = _next_step_hint(1000.0, 100_000.0, 5.0)  # proportional cash draw exceeds the gross withdrawal
    assert 'Fund the full 1000.00' in hint
    assert 'share-sell' not in hint


def test_next_step_hint_multiple_rates_uses_placeholder() -> None:
    hint = _next_step_hint(None, 200.0, None)
    assert 'Pick a row' in hint
    assert '--target-gross <grossPerYear>' in hint


def _with_cash_and_taxonomy(portfolio: Portfolio, cash: float, assignment_rows: list[list[object]], retired: bool = False) -> Portfolio:
    deposit_accounts, deposit_transactions = _deposit_account(cash, retired=retired)
    assignments = pd.DataFrame(assignment_rows, columns=['taxonomyName', 'itemId', 'itemType', 'categoryName', 'weight'])
    return Portfolio(
        accounts=pd.concat([portfolio.securities_accounts, deposit_accounts]),
        transactions=pd.concat([portfolio.securities_account_transactions, deposit_transactions]),
        securities=portfolio.securities,
        prices=portfolio.prices,
        taxonomy_assignments=assignments,
    )


def test_blended_return_weights_allocation_and_dilutes_unclassified_cash(portfolio_with_prices: Portfolio) -> None:
    portfolio = _with_cash_and_taxonomy(portfolio_with_prices, 1000.0, [['AA', 'sec-1', 'security', 'Equity', 10000]])

    blended = blended_return_from_allocation(portfolio, _DATE, 'AA', {'Equity': 5.0})

    assert blended == pytest.approx(9000.0 * 5.0 / 10000.0)  # 9000 securities at 5%, 1000 unclassified cash at 0%


def test_blended_return_uses_class_of_assigned_cash_account(portfolio_with_prices: Portfolio) -> None:
    portfolio = _with_cash_and_taxonomy(portfolio_with_prices, 1000.0, [
        ['AA', 'sec-1', 'security', 'Equity', 10000],
        ['AA', 'dep-1', 'account', 'Cash', 10000],
    ])

    blended = blended_return_from_allocation(portfolio, _DATE, 'AA', {'Equity': 5.0, 'Cash': 1.0})

    assert blended == pytest.approx((9000.0 * 5.0 + 1000.0 * 1.0) / 10000.0)


def test_split_return_scenario_separates_default_from_overrides() -> None:
    assert split_return_scenario({'*': 4.0, 'Equity': 5.0}) == (4.0, {'Equity': 5.0})
    assert split_return_scenario({'Equity': 5.0}) == (None, {'Equity': 5.0})
    assert split_return_scenario({'*': 3.0}) == (3.0, {})


def test_blended_return_default_rate_fills_unlisted_category(portfolio_with_prices: Portfolio, caplog: pytest.LogCaptureFixture) -> None:
    portfolio = _with_cash_and_taxonomy(portfolio_with_prices, 1000.0, [
        ['AA', 'sec-1', 'security', 'Equity', 10000],
        ['AA', 'dep-1', 'account', 'Cash', 10000],
    ])

    with caplog.at_level(logging.WARNING):
        blended = blended_return_from_allocation(portfolio, _DATE, 'AA', {'*': 2.0, 'Equity': 5.0})

    assert blended == pytest.approx((9000.0 * 5.0 + 1000.0 * 2.0) / 10000.0)  # Cash falls back to the '*' default
    assert 'No return configured' not in caplog.text  # the default covers it, so no warning


def test_blended_return_default_only_applies_to_every_category(portfolio_with_prices: Portfolio) -> None:
    portfolio = _with_cash_and_taxonomy(portfolio_with_prices, 1000.0, [
        ['AA', 'sec-1', 'security', 'Equity', 10000],
        ['AA', 'dep-1', 'account', 'Cash', 10000],
    ])

    assert blended_return_from_allocation(portfolio, _DATE, 'AA', {'*': 3.0}) == pytest.approx(3.0)


def test_blended_return_unclassified_security_contributes_zero(portfolio_with_prices: Portfolio) -> None:
    portfolio = _with_cash_and_taxonomy(portfolio_with_prices, 1000.0, [['AA', 'sec-other', 'security', 'Equity', 10000]])

    assert blended_return_from_allocation(portfolio, _DATE, 'AA', {'Equity': 5.0}) == pytest.approx(0.0)


def test_blended_return_unconfigured_class_contributes_zero(portfolio_with_prices: Portfolio) -> None:
    portfolio = _with_cash_and_taxonomy(portfolio_with_prices, 1000.0, [['AA', 'sec-1', 'security', 'Equity', 10000]])

    assert blended_return_from_allocation(portfolio, _DATE, 'AA', {'Bonds': 2.0}) == pytest.approx(0.0)


def _retire_securities_account(portfolio: Portfolio, account_id: str) -> Portfolio:
    accounts = portfolio.securities_accounts.copy()
    accounts.loc[account_id, 'isRetired'] = True

    return Portfolio(
        accounts=accounts,
        transactions=portfolio.securities_account_transactions,
        securities=portfolio.securities,
        prices=portfolio.prices,
        taxonomy_assignments=portfolio.taxonomy_assignments,
    )


def test_start_capital_excludes_retired_securities_account(portfolio_with_prices: Portfolio) -> None:
    portfolio = _retire_securities_account(portfolio_with_prices, 'acc-2')  # holds the 5 gifted shares

    result = prepare_pmt_result(portfolio, empty_config(), _DATE, _TAX_RATE, [5.0], _END_DATE, allowance=0.0)

    assert result.iloc[0]['startCapital'] == pytest.approx(8000.0)  # 40 shares @ 200 left in acc-1


def test_blended_return_excludes_retired_securities_account(portfolio_with_prices: Portfolio) -> None:
    portfolio = _with_cash_and_taxonomy(_retire_securities_account(portfolio_with_prices, 'acc-2'), 1000.0, [
        ['AA', 'sec-1', 'security', 'Equity', 10000],
        ['AA', 'dep-1', 'account', 'Cash', 10000],
    ])

    blended = blended_return_from_allocation(portfolio, _DATE, 'AA', {'Equity': 5.0, 'Cash': 1.0})

    assert blended == pytest.approx((8000.0 * 5.0 + 1000.0 * 1.0) / 9000.0)  # retired account drops out of the weights


def test_blended_return_excludes_retired_cash_without_warning(portfolio_with_prices: Portfolio, caplog: pytest.LogCaptureFixture) -> None:
    portfolio = _with_cash_and_taxonomy(portfolio_with_prices, 1000.0, [['AA', 'sec-1', 'security', 'Equity', 10000]], retired=True)

    with caplog.at_level(logging.WARNING):
        blended = blended_return_from_allocation(portfolio, _DATE, 'AA', {'Equity': 5.0})

    assert blended == pytest.approx(5.0)  # retired cash neither dilutes the blend...
    assert 'not classified' not in caplog.text  # ...nor triggers the unclassified-account warning


def test_resolve_return_scenarios_mixes_fixed_and_blended(portfolio_with_prices: Portfolio) -> None:
    portfolio = _with_cash_and_taxonomy(portfolio_with_prices, 1000.0, [['AA', 'sec-1', 'security', 'Equity', 10000]])

    scenarios = _resolve_return_scenarios(portfolio, 'AA', _DATE, [2.0, 6.0, {'Equity': 5.0}])

    assert scenarios[:2] == [2.0, 6.0]  # fixed rates kept in order
    assert scenarios[2] == pytest.approx(9000.0 * 5.0 / 10000.0)  # per-category entry blended into one rate


def test_resolve_return_scenarios_per_category_entry_requires_taxonomy(portfolio_with_prices: Portfolio) -> None:
    with pytest.raises(InputError, match="taxonomy"):
        _resolve_return_scenarios(portfolio_with_prices, None, _DATE, [{'Equity': 5.0}])


def test_prepare_pmt_result_ignores_retired_cash(portfolio_with_prices: Portfolio) -> None:
    deposit_accounts, deposit_transactions = _deposit_account(100000.0, retired=True)
    portfolio = Portfolio(
        accounts=pd.concat([portfolio_with_prices.securities_accounts, deposit_accounts]),
        transactions=pd.concat([portfolio_with_prices.securities_account_transactions, deposit_transactions]),
        securities=portfolio_with_prices.securities,
        prices=portfolio_with_prices.prices,
    )

    result = prepare_pmt_result(portfolio, empty_config(), _DATE, _TAX_RATE, [5.0], _END_DATE, allowance=0.0)

    # retired cash must not inflate the withdrawal base: identical to the securities-only result (9000 market value)
    assert result.iloc[0]['grossPerYear'] == pytest.approx(9000.0 * amortization_factor(0.05, _HORIZON_YEARS))


def test_prepare_pmt_result_negative_cash_cancels_depot(portfolio_with_prices: Portfolio) -> None:
    deposit_accounts, deposit_transactions = _deposit_account(-20000.0)
    portfolio = Portfolio(
        accounts=pd.concat([portfolio_with_prices.securities_accounts, deposit_accounts]),
        transactions=pd.concat([portfolio_with_prices.securities_account_transactions, deposit_transactions]),
        securities=portfolio_with_prices.securities,
        prices=portfolio_with_prices.prices,
    )

    with pytest.raises(InputError, match="nothing left to withdraw"):
        prepare_pmt_result(portfolio, empty_config(), _DATE, _TAX_RATE, [5.0], _END_DATE)

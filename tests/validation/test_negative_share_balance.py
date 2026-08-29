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
# pylint: disable=duplicate-code

from datetime import datetime

import pandas as pd
import pytest

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import AccountType, TransactionType
from pp_terminal.validation.engine import validate_securities, ValidationResult
from pp_terminal.utils.config import load_config


@pytest.fixture(name='portfolio_with_negative_balances')
def provide_portfolio_with_negative_balances() -> Portfolio:
    """Portfolio with a healthy, an oversold, a cross-account inconsistent, a flat, a dust-residue and a forex-legged security."""
    accounts = pd.DataFrame([
        ['Account 1', AccountType.SECURITIES.value, None, False, 'EUR'],
        ['Account 2', AccountType.SECURITIES.value, None, False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
       index=['acc-1', 'acc-2'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Security OK', 'AAA', 'ISIN-A', None, False, 'EUR'],
        ['Security Oversold', 'BBB', 'ISIN-B', None, False, 'EUR'],
        ['Security Cross Account', 'CCC', 'ISIN-C', None, False, 'EUR'],
        ['Security Flat', 'DDD', 'ISIN-D', None, False, 'EUR'],
        ['Security Dust', 'EEE', 'ISIN-E', None, False, 'EUR'],
        ['Security Forex', 'FFF', 'ISIN-F', None, False, 'USD'],
    ], columns=['name', 'wkn', 'isin', 'note', 'isRetired', 'currency'],
       index=['sec-ok', 'sec-oversold', 'sec-cross', 'sec-flat', 'sec-dust', 'sec-forex'])
    securities.index.name = 'securityId'

    prices = pd.DataFrame([
        [datetime(2023, 1, 1), 'sec-ok', 100.0],
        [datetime(2023, 1, 1), 'sec-oversold', 100.0],
        [datetime(2023, 1, 1), 'sec-cross', 100.0],
        [datetime(2023, 1, 1), 'sec-flat', 100.0],
        [datetime(2023, 1, 1), 'sec-dust', 100.0],
        [datetime(2023, 1, 1), 'sec-forex', 100.0],
    ], columns=['date', 'securityId', 'price'])
    prices = prices.set_index(['date', 'securityId'])

    transactions = pd.DataFrame([
        # healthy long position
        [datetime(2020, 1, 15), 'acc-1', 'sec-ok', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        # oversold: bought 10, sold 13 -> -3
        [datetime(2020, 1, 15), 'acc-1', 'sec-oversold', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2021, 2, 1), 'acc-1', 'sec-oversold', TransactionType.SELL.value, 1300.0, 13.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        # cross-account inconsistency: +10 in acc-1, -3 in acc-2 (net positive)
        [datetime(2020, 1, 15), 'acc-1', 'sec-cross', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2021, 2, 1), 'acc-2', 'sec-cross', TransactionType.SELL.value, 300.0, 3.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        # flat: fully sold -> 0
        [datetime(2020, 1, 15), 'acc-1', 'sec-flat', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2021, 2, 1), 'acc-1', 'sec-flat', TransactionType.SELL.value, 1100.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        # dust residue below tolerance: -0.0005
        [datetime(2020, 1, 15), 'acc-1', 'sec-dust', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2021, 2, 1), 'acc-1', 'sec-dust', TransactionType.SELL.value, 1000.0, 10.0005, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        # forex legs: buy recorded in EUR, sell carries forex currency USD -> net 0 shares despite split currency slices
        [datetime(2020, 1, 15), 'acc-1', 'sec-forex', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2021, 2, 1), 'acc-1', 'sec-forex', TransactionType.SELL.value, 1100.0, 10.0, AccountType.SECURITIES.value, 'USD', 0.0, 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    return Portfolio(
        accounts=accounts,
        transactions=transactions,
        securities=securities,
        prices=prices
    )


@pytest.fixture(name='validation_results')
def provide_validation_results(portfolio_with_negative_balances: Portfolio) -> dict[str, ValidationResult]:
    """Built-in rule run with empty configuration, evaluated per 2023-01-01."""
    snapshot = PortfolioSnapshot(portfolio_with_negative_balances, datetime(2023, 1, 1))
    return validate_securities(portfolio_with_negative_balances, snapshot, load_config({}))


def test_negative_balance_warning_without_config(validation_results: dict[str, ValidationResult]) -> None:
    """The rule is built-in and must run even with an empty configuration."""
    assert len(validation_results['sec-oversold'].violations) == 1
    assert 'negative share balance' in validation_results['sec-oversold'].messages
    assert '-3.00 in "Account 1"' in validation_results['sec-oversold'].messages


def test_negative_balance_is_warning_not_error(validation_results: dict[str, ValidationResult]) -> None:
    assert not validation_results['sec-oversold'].has_errors


def test_negative_balance_in_one_account_despite_positive_net(validation_results: dict[str, ValidationResult]) -> None:
    """A negative balance in one account must be flagged even if the net across accounts is positive."""
    assert len(validation_results['sec-cross'].violations) == 1
    assert '-3.00 in "Account 2"' in validation_results['sec-cross'].messages


def test_no_warning_for_healthy_flat_and_dust_positions(validation_results: dict[str, ValidationResult]) -> None:
    assert not validation_results['sec-ok'].violations
    assert not validation_results['sec-flat'].violations
    assert not validation_results['sec-dust'].violations  # residue below tolerance


def test_no_warning_for_forex_legs_netting_to_zero(validation_results: dict[str, ValidationResult]) -> None:
    """Buy and sell legs recorded in different currencies must be netted per account, not per currency slice."""
    assert not validation_results['sec-forex'].violations


def test_balances_are_evaluated_per_snapshot_date(portfolio_with_negative_balances: Portfolio) -> None:
    """Before the oversell happened, the same portfolio must validate clean."""
    snapshot = PortfolioSnapshot(portfolio_with_negative_balances, datetime(2020, 6, 1))
    results = validate_securities(portfolio_with_negative_balances, snapshot, load_config({}))

    assert not results['sec-oversold'].violations


def test_user_configured_rule_replaces_built_in(portfolio_with_negative_balances: Portfolio) -> None:
    config = {'commands': {'validate': {'securities': {'rules': [
        {'type': 'negative-share-balance', 'severity': 'error', 'tolerance': 0.001},
    ]}}}}
    snapshot = PortfolioSnapshot(portfolio_with_negative_balances, datetime(2023, 1, 1))
    results = validate_securities(portfolio_with_negative_balances, snapshot, load_config(config))

    assert len(results['sec-oversold'].violations) == 1
    assert results['sec-oversold'].has_errors


def test_built_in_rule_can_be_disabled_via_valid_months(portfolio_with_negative_balances: Portfolio) -> None:
    config = {'commands': {'validate': {'securities': {'rules': [
        {'type': 'negative-share-balance', 'valid-months': []},
    ]}}}}
    snapshot = PortfolioSnapshot(portfolio_with_negative_balances, datetime(2023, 1, 1))
    results = validate_securities(portfolio_with_negative_balances, snapshot, load_config(config))

    assert not results['sec-oversold'].violations


def test_share_balances_include_negatives_but_shares_do_not(portfolio_with_negative_balances: Portfolio) -> None:
    """snapshot.shares keeps its positive-only contract while share_balances exposes the raw sums."""
    snapshot = PortfolioSnapshot(portfolio_with_negative_balances, datetime(2023, 1, 1))

    balances = snapshot.share_balances.groupby('securityId').sum()
    assert balances.loc['sec-oversold'] == pytest.approx(-3.0)
    assert balances.loc['sec-cross'] == pytest.approx(7.0)

    shares = snapshot.shares
    assert (shares > 0).all()
    assert 'sec-oversold' not in shares.index.get_level_values('securityId')

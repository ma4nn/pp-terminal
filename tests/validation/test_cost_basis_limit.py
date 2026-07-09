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

import logging
from datetime import datetime
from typing import Any

import pandas as pd
import pytest

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import AccountType, TransactionType
from pp_terminal.validation.engine import validate_securities, ValidationResult
from pp_terminal.validation.rules import CostBasisLimitRule


def _validate_securities(portfolio: Portfolio, config: dict[str, Any]) -> dict[str, ValidationResult]:
    return validate_securities(portfolio, PortfolioSnapshot(portfolio, datetime(2023, 12, 31)), config)


@pytest.fixture(name='portfolio_with_purchases_and_sales')
def provide_portfolio_with_purchases_and_sales() -> Portfolio:
    """Portfolio with multiple securities, purchases, and sales."""
    accounts = pd.DataFrame([
        ['Account 1', AccountType.SECURITIES.value, None, False, 'EUR'],
        ['Account 2', AccountType.SECURITIES.value, None, False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
       index=['acc-1', 'acc-2'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Security A', 'AAA', 'ISIN-A', None, False, 'EUR'],
        ['Security B', 'BBB', 'ISIN-B', None, False, 'EUR'],
        ['Security C', 'CCC', 'ISIN-C', None, False, 'EUR'],
    ], columns=['name', 'wkn', 'isin', 'note', 'isRetired', 'currency'],
       index=['sec-a', 'sec-b', 'sec-c'])
    securities.index.name = 'securityId'

    # Add price data (required by validate_securities)
    prices = pd.DataFrame([
        [datetime(2023, 1, 1), 'sec-a', 120.0],
        [datetime(2023, 1, 1), 'sec-b', 105.0],
        [datetime(2023, 1, 1), 'sec-c', 100.0],
    ], columns=['date', 'securityId', 'price'])
    prices = prices.set_index(['date', 'securityId'])

    # Security A: €4,500 cost basis (10 shares @ €150, 30 @ €100, sold 10)
    # Security B: €2,000 cost basis (20 shares @ €100)
    # Security C: €0 cost basis (all shares sold)
    transactions = pd.DataFrame([
        # Security A purchases
        [datetime(2020, 1, 15), 'acc-1', 'sec-a', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2020, 6, 20), 'acc-1', 'sec-a', TransactionType.BUY.value, -1500.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2021, 3, 10), 'acc-1', 'sec-a', TransactionType.BUY.value, -3000.0, 30.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        # Security A sale (FIFO: consume 10 @ €100)
        [datetime(2022, 1, 5), 'acc-1', 'sec-a', TransactionType.SELL.value, 1200.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        # Security B purchases
        [datetime(2020, 2, 1), 'acc-2', 'sec-b', TransactionType.BUY.value, -2000.0, 20.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        # Security C purchases and full sale
        [datetime(2020, 3, 1), 'acc-1', 'sec-c', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2022, 1, 1), 'acc-1', 'sec-c', TransactionType.SELL.value, 1500.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    return Portfolio(
        accounts=accounts,
        transactions=transactions,
        securities=securities,
        prices=prices
    )


@pytest.fixture(name='tax_csv_data_df')
def provide_tax_csv_data_df() -> pd.DataFrame:
    """Tax CSV data for testing."""
    data = pd.DataFrame([
        [2020, 'acc-1', 'sec-a', 0.379147],
        [2021, 'acc-1', 'sec-a', 0.454976],
    ], columns=['year', 'account_id', 'security_id', 'deemed_income'])
    return data.set_index(['year', 'account_id', 'security_id'])


def test_no_validation_config(portfolio_with_purchases_and_sales: Portfolio) -> None:
    """Test that validation completes successfully when no validation config exists."""
    results = _validate_securities(portfolio_with_purchases_and_sales, {})

    assert len(results) == 3
    assert all(not result.has_errors for result in results.values())


def test_cost_basis_limit_pass(portfolio_with_purchases_and_sales: Portfolio) -> None:
    """Test cost basis limit validation when all securities pass."""
    config = {
        'commands': {
            'validate': {
                'securities': {
                    'rules': [
                        {'type': 'cost-basis-limit', 'value': 10000.0}
                    ]
                }
            }
        }
    }

    results = _validate_securities(portfolio_with_purchases_and_sales, config)

    # All securities should pass (sec-a: €4500, sec-b: €2000, sec-c: €0)
    assert all(not result.has_errors for result in results.values())


def test_cost_basis_limit_fail(portfolio_with_purchases_and_sales: Portfolio) -> None:
    """Test cost basis limit validation when security exceeds limit."""
    config = {
        'commands': {
            'validate': {
                'securities': {
                    'rules': [
                        {'type': 'cost-basis-limit', 'value': 3000.0}
                    ]
                }
            }
        }
    }

    results = _validate_securities(portfolio_with_purchases_and_sales, config)

    # sec-a should fail
    assert results['sec-a'].has_errors
    assert 'current cost basis' in results['sec-a'].messages
    assert '4500.00' in results['sec-a'].messages
    assert 'exceeds limit 3000.00' in results['sec-a'].messages

    # sec-b and sec-c should pass
    assert not results['sec-b'].has_errors
    assert not results['sec-c'].has_errors


def test_entity_specific_rule(portfolio_with_purchases_and_sales: Portfolio) -> None:
    """Test entity-specific rules with applies-to."""
    config = {
        'commands': {
            'validate': {
                'securities': {
                    'rules': [
                        {'type': 'cost-basis-limit', 'value': 3000.0, 'applies-to': ['sec-a']}
                    ]
                }
            }
        }
    }

    results = _validate_securities(portfolio_with_purchases_and_sales, config)

    # Only sec-a should be validated and fail
    assert results['sec-a'].has_errors
    # sec-b and sec-c should not have violations (rule doesn't apply)
    assert not results['sec-b'].has_errors
    assert not results['sec-c'].has_errors


def test_attribute_based_rule(portfolio_with_purchases_and_sales: Portfolio) -> None:
    """Test cost-basis-limit-from-attribute rule type."""
    test_attr_uuid = 'test-attr-uuid-12345'

    # Add custom attribute with limit for sec-a and sec-b
    securities_df = portfolio_with_purchases_and_sales.securities.copy()
    securities_df.loc['sec-a', test_attr_uuid] = 5000.0  # sec-a will pass
    securities_df.loc['sec-b', test_attr_uuid] = 1500.0  # sec-b will fail

    modified_portfolio = Portfolio(
        accounts=portfolio_with_purchases_and_sales.securities_accounts,
        transactions=portfolio_with_purchases_and_sales.securities_account_transactions,
        securities=securities_df,
        prices=portfolio_with_purchases_and_sales.prices
    )

    config = {
        'commands': {
            'validate': {
                'securities': {
                    'rules': [
                        {'type': 'cost-basis-limit-from-attribute', 'value': test_attr_uuid}
                    ]
                }
            }
        }
    }

    results = _validate_securities(modified_portfolio, config)

    # sec-a should pass
    assert not results['sec-a'].has_errors
    # sec-b should fail
    assert results['sec-b'].has_errors
    assert '2000.00' in results['sec-b'].messages
    assert 'exceeds limit 1500.00' in results['sec-b'].messages


def test_warning_severity(portfolio_with_purchases_and_sales: Portfolio) -> None:
    """Test that warning severity doesn't raise an error."""
    config = {
        'commands': {
            'validate': {
                'securities': {
                    'rules': [
                        {'type': 'cost-basis-limit', 'value': 3000.0, 'severity': 'warning'}
                    ]
                }
            }
        }
    }

    results = _validate_securities(portfolio_with_purchases_and_sales, config)

    # sec-a should have a violation but not an error (warning severity)
    assert len(results['sec-a'].violations) > 0
    assert not results['sec-a'].has_errors
    assert '⚠️' in results['sec-a'].messages


def test_mixed_severities(portfolio_with_purchases_and_sales: Portfolio) -> None:
    """Test mixed warning and error severities."""
    config = {
        'commands': {
            'validate': {
                'securities': {
                    'rules': [
                        {'type': 'cost-basis-limit', 'value': 3000.0, 'severity': 'warning', 'applies-to': ['sec-a']},
                        {'type': 'cost-basis-limit', 'value': 1500.0, 'applies-to': ['sec-b']}
                    ]
                }
            }
        }
    }

    results = _validate_securities(portfolio_with_purchases_and_sales, config)

    # sec-a should have warning
    assert len(results['sec-a'].violations) > 0
    assert not results['sec-a'].has_errors

    # sec-b should have error
    assert results['sec-b'].has_errors


def test_multiple_accounts_aggregated(portfolio_with_purchases_and_sales: Portfolio) -> None:
    """Test that cost is aggregated across multiple accounts."""
    # Add purchases for sec-a in acc-2
    additional_transactions = pd.DataFrame([
        [datetime(2020, 1, 1), 'acc-2', 'sec-a', TransactionType.BUY.value, -500.0, 5.0, AccountType.SECURITIES.value, 'EUR', 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes'])
    additional_transactions = additional_transactions.set_index(['date', 'accountId', 'securityId'])

    transactions = pd.concat([portfolio_with_purchases_and_sales.securities_account_transactions, additional_transactions])

    portfolio = Portfolio(
        accounts=portfolio_with_purchases_and_sales.securities_accounts,
        transactions=transactions,
        securities=portfolio_with_purchases_and_sales.securities,
        prices=portfolio_with_purchases_and_sales.prices
    )

    config = {
        'commands': {
            'validate': {
                'securities': {
                    'rules': [
                        {'type': 'cost-basis-limit', 'value': 4500.0}
                    ]
                }
            }
        }
    }

    results = _validate_securities(portfolio, config)

    # sec-a should fail (acc-1: €5500 + acc-2: €500 = €6000 > €4500)
    assert results['sec-a'].has_errors
    assert '5000.00' in results['sec-a'].messages


def test_cost_basis_limit_rule_direct(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)

    rule = CostBasisLimitRule(
        rule_type='cost-basis-limit',
        value=1000.0,
        severity='error',
        applies_to=None
    )

    entity = pd.Series({
        'name': 'Test Security',
        'currency': 'EUR'
    })

    with pytest.raises(RuntimeError):
        rule.validate(entity, 'sec-1', {})

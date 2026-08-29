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
from pp_terminal.utils.config import load_config
from pp_terminal.validation.engine import validate_securities, ValidationResult

_COLUMNS = ['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees', 'transferTargetAccount']


@pytest.fixture(name='validation_results')
def provide_validation_results() -> dict[str, ValidationResult]:
    """Portfolio with a linked transfer, an unlinked (None) transfer, an empty-string transfer and a plain holding."""
    accounts = pd.DataFrame([
        ['Depot 1', AccountType.SECURITIES.value, None, False, 'EUR'],
        ['Depot 2', AccountType.SECURITIES.value, None, False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'], index=['depot1', 'depot2'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Security Linked', 'AAA', 'ISIN-A', None, False, 'EUR'],
        ['Security Unlinked', 'BBB', 'ISIN-B', None, False, 'EUR'],
        ['Security Empty', 'CCC', 'ISIN-C', None, False, 'EUR'],
        ['Security Plain', 'DDD', 'ISIN-D', None, False, 'EUR'],
    ], columns=['name', 'wkn', 'isin', 'note', 'isRetired', 'currency'],
       index=['sec-linked', 'sec-unlinked', 'sec-empty', 'sec-plain'])
    securities.index.name = 'securityId'

    prices = pd.DataFrame([
        [datetime(2023, 1, 1), sid, 100.0] for sid in ['sec-linked', 'sec-unlinked', 'sec-empty', 'sec-plain']
    ], columns=['date', 'securityId', 'price']).set_index(['date', 'securityId'])

    transactions = pd.DataFrame([
        [datetime(2020, 1, 15), 'depot1', 'sec-linked', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2021, 1, 15), 'depot1', 'sec-linked', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot2'],
        [datetime(2021, 1, 15), 'depot2', 'sec-linked', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2020, 1, 15), 'depot1', 'sec-unlinked', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2021, 1, 15), 'depot1', 'sec-unlinked', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2020, 1, 15), 'depot1', 'sec-empty', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2021, 1, 15), 'depot1', 'sec-empty', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, ''],
        [datetime(2020, 1, 15), 'depot1', 'sec-plain', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ], columns=_COLUMNS).set_index(['date', 'accountId', 'securityId'])

    portfolio = Portfolio(accounts=accounts, transactions=transactions, securities=securities, prices=prices)
    snapshot = PortfolioSnapshot(portfolio, datetime(2023, 1, 1))
    return validate_securities(portfolio, snapshot, load_config({}))


def test_unlinked_transfer_is_flagged(validation_results: dict[str, ValidationResult]) -> None:
    assert len(validation_results['sec-unlinked'].violations) == 1
    assert 'no linked destination account' in validation_results['sec-unlinked'].messages
    assert '10.00 from "Depot 1"' in validation_results['sec-unlinked'].messages


def test_empty_string_target_is_flagged(validation_results: dict[str, ValidationResult]) -> None:
    """An empty (non-null) destination account must be treated the same as a missing link."""
    assert len(validation_results['sec-empty'].violations) == 1
    assert 'no linked destination account' in validation_results['sec-empty'].messages


def test_unlinked_transfer_is_warning_not_error(validation_results: dict[str, ValidationResult]) -> None:
    assert not validation_results['sec-unlinked'].has_errors
    assert validation_results['sec-unlinked'].violations


def test_properly_linked_transfer_is_not_flagged(validation_results: dict[str, ValidationResult]) -> None:
    assert not validation_results['sec-linked'].violations


def test_security_without_transfer_is_not_flagged(validation_results: dict[str, ValidationResult]) -> None:
    assert not validation_results['sec-plain'].violations

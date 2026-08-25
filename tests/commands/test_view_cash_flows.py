"""
    Copyright (C) 2025-26 Dipl.-Ing. Christoph Massmann <chris@dev-investor.de>

    This file is part of pp-terminal.

    pp-terminal is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
"""
# pylint: disable=duplicate-code

import json
from datetime import datetime
from typing import Any

import pandas as pd
import pytest
from _pytest.fixtures import TopRequest
from typer.testing import CliRunner

from pp_terminal.commands.view_cash_flows import prepare_cash_flows_df, resolve_deposit_account
from pp_terminal.data.pp_portfolio_builder import PpPortfolioBuilder
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import AccountType, TransactionType
from pp_terminal.exceptions import InputError
from pp_terminal.main import app

_LATER_THAN_ANY_TRANSACTION = datetime(2030, 1, 1)


def _portfolio(account_names: list[str], transactions: list[list[Any]] | None = None) -> Portfolio:
    accounts = pd.DataFrame(
        [[name, AccountType.DEPOSIT.value, None, False, 'EUR'] for name in account_names]
        + [['Testdepot', AccountType.SECURITIES.value, None, False, 'EUR']],
        columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
        index=[f'account-{index}' for index in range(len(account_names))] + ['securities-1'],
    )
    accounts.index.name = 'accountId'

    transactions_df = pd.DataFrame(
        transactions or [],
        columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'],
    ).set_index(['date', 'accountId', 'securityId'])

    return Portfolio(accounts=accounts, transactions=transactions_df)


def _deposit(date: datetime, account_id: str, transaction_type: TransactionType, amount: float, currency: str) -> list[Any]:
    return [date, account_id, None, transaction_type.value, amount, 0.0, AccountType.DEPOSIT.value, currency, 0.0, 0.0]


def _sample_portfolio() -> Portfolio:
    return _portfolio(['Girokonto', 'Zweitkonto'], [
        _deposit(datetime(2024, 1, 1), 'account-0', TransactionType.DEPOSIT, 1000.0, 'EUR'),
        _deposit(datetime(2024, 2, 1), 'account-0', TransactionType.REMOVAL, -250.0, 'EUR'),
        _deposit(datetime(2024, 3, 1), 'account-0', TransactionType.TRANSFER_IN, 500.0, 'EUR'),
        _deposit(datetime(2024, 4, 1), 'account-1', TransactionType.DEPOSIT, 300.0, 'USD'),
        _deposit(datetime(2024, 5, 1), 'account-1', TransactionType.REMOVAL, -50.0, 'USD'),
    ])


def test_excludes_transfers_and_separates_currencies() -> None:
    result = prepare_cash_flows_df(_sample_portfolio(), _LATER_THAN_ANY_TRANSACTION)

    assert result.to_dict('records') == [
        {'currency': 'EUR', 'totalDeposits': 1000.0, 'totalWithdrawals': 250.0, 'netContributions': 750.0, 'transactionCount': 2},
        {'currency': 'USD', 'totalDeposits': 300.0, 'totalWithdrawals': 50.0, 'netContributions': 250.0, 'transactionCount': 2},
    ]


def test_can_include_transfers_and_filter_account() -> None:
    result = prepare_cash_flows_df(_sample_portfolio(), _LATER_THAN_ANY_TRANSACTION, 'account-0', include_transfers=True)

    assert result.to_dict('records') == [
        {'currency': 'EUR', 'totalDeposits': 1500.0, 'totalWithdrawals': 250.0, 'netContributions': 1250.0, 'transactionCount': 3},
    ]


def test_counts_only_transactions_up_to_the_given_date() -> None:
    result = prepare_cash_flows_df(_sample_portfolio(), datetime(2024, 1, 31))

    assert result.to_dict('records') == [
        {'currency': 'EUR', 'totalDeposits': 1000.0, 'totalWithdrawals': 0.0, 'netContributions': 1000.0, 'transactionCount': 1},
    ]


def test_net_contributions_turn_negative_when_withdrawals_exceed_deposits() -> None:
    portfolio = _portfolio(['Girokonto'], [
        _deposit(datetime(2024, 1, 1), 'account-0', TransactionType.DEPOSIT, 100.0, 'EUR'),
        _deposit(datetime(2024, 2, 1), 'account-0', TransactionType.REMOVAL, -400.0, 'EUR'),
    ])

    result = prepare_cash_flows_df(portfolio, _LATER_THAN_ANY_TRANSACTION)

    assert result.to_dict('records') == [
        {'currency': 'EUR', 'totalDeposits': 100.0, 'totalWithdrawals': 400.0, 'netContributions': -300.0, 'transactionCount': 2},
    ]


def test_ignores_transaction_types_that_are_not_external_cash_flows() -> None:
    portfolio = _portfolio(['Girokonto'], [
        _deposit(datetime(2024, 1, 1), 'account-0', TransactionType.DEPOSIT, 1000.0, 'EUR'),
        _deposit(datetime(2024, 2, 1), 'account-0', TransactionType.DIVIDENDS, 50.0, 'EUR'),
        _deposit(datetime(2024, 3, 1), 'account-0', TransactionType.INTEREST, 10.0, 'EUR'),
        _deposit(datetime(2024, 4, 1), 'account-0', TransactionType.FEES, -5.0, 'EUR'),
        _deposit(datetime(2024, 5, 1), 'account-0', TransactionType.BUY, -800.0, 'EUR'),
    ])

    result = prepare_cash_flows_df(portfolio, _LATER_THAN_ANY_TRANSACTION)

    assert result.to_dict('records') == [
        {'currency': 'EUR', 'totalDeposits': 1000.0, 'totalWithdrawals': 0.0, 'netContributions': 1000.0, 'transactionCount': 1},
    ]


def test_portfolio_without_cash_flows_yields_empty_result() -> None:
    result = prepare_cash_flows_df(_portfolio(['Girokonto']), _LATER_THAN_ANY_TRANSACTION)

    assert len(result) == 0
    assert list(result.columns) == ['currency', 'totalDeposits', 'totalWithdrawals', 'netContributions', 'transactionCount']


def test_matches_the_deposits_in_the_sample_file(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

    result = prepare_cash_flows_df(portfolio, _LATER_THAN_ANY_TRANSACTION)

    assert result.to_dict('records') == [
        {'currency': 'EUR', 'totalDeposits': 11500.0, 'totalWithdrawals': 0.0, 'netContributions': 11500.0, 'transactionCount': 3},
        {'currency': 'GBP', 'totalDeposits': 2000.0, 'totalWithdrawals': 0.0, 'netContributions': 2000.0, 'transactionCount': 1},
        {'currency': 'USD', 'totalDeposits': 3000.0, 'totalWithdrawals': 0.0, 'netContributions': 3000.0, 'transactionCount': 1},
    ]


def test_resolve_deposit_account_accepts_id_and_name() -> None:
    portfolio = _portfolio(['Girokonto', 'Verrechnungskonto'])

    assert resolve_deposit_account(portfolio, 'account-1') == 'account-1'
    assert resolve_deposit_account(portfolio, 'Girokonto') == 'account-0'


def test_resolve_deposit_account_rejects_unknown_account() -> None:
    with pytest.raises(InputError, match="'nope' not found"):
        resolve_deposit_account(_portfolio(['Girokonto']), 'nope')


def test_resolve_deposit_account_rejects_securities_account() -> None:
    with pytest.raises(InputError, match="'securities-1' not found"):
        resolve_deposit_account(_portfolio(['Girokonto']), 'securities-1')


def test_resolve_deposit_account_rejects_ambiguous_name() -> None:
    with pytest.raises(InputError, match='matches multiple deposit accounts'):
        resolve_deposit_account(_portfolio(['Girokonto', 'Girokonto']), 'Girokonto')


def test_cli_reports_cash_flows_for_one_account(request: TopRequest) -> None:
    xml_file = request.path.parent.parent / 'fixtures' / 'kommer.ids.xml'

    result = CliRunner().invoke(app, [
        '--file', str(xml_file), '--output', 'json', '--no-cache',
        'view', 'cash-flows', '--account', 'Tagesgeld',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert json.loads(result.output) == [
        {'currency': 'EUR', 'totalDeposits': 500.0, 'totalWithdrawals': 0.0, 'netContributions': 500.0, 'transactionCount': 1},
    ]


def test_cli_rejects_an_unknown_account(request: TopRequest) -> None:
    xml_file = request.path.parent.parent / 'fixtures' / 'kommer.ids.xml'

    result = CliRunner().invoke(app, [
        '--file', str(xml_file), '--output', 'json', '--no-cache',
        'view', 'cash-flows', '--account', 'Nope',
    ])

    assert result.exit_code != 0

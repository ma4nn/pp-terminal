"""
    Copyright (C) 2025-26 Dipl.-Ing. Christoph Massmann <chris@dev-investor.de>

    This file is part of pp-terminal.

    pp-terminal is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
"""

from datetime import datetime

import pandas as pd
import pytest

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import AccountType, TransactionType
from pp_terminal.exceptions import InputError
from pp_terminal.mcp_server import _calculate_cash_flows, _resolve_deposit_account


def _transactions() -> pd.DataFrame:
    return pd.DataFrame([
        [datetime(2024, 1, 1), 'account-1', TransactionType.DEPOSIT.value, 1000.0, 'EUR'],
        [datetime(2024, 2, 1), 'account-1', TransactionType.REMOVAL.value, -250.0, 'EUR'],
        [datetime(2024, 3, 1), 'account-1', TransactionType.TRANSFER_IN.value, 500.0, 'EUR'],
        [datetime(2024, 4, 1), 'account-2', TransactionType.DEPOSIT.value, 300.0, 'USD'],
        [datetime(2024, 5, 1), 'account-2', TransactionType.REMOVAL.value, -50.0, 'USD'],
    ], columns=['date', 'accountId', 'type', 'amount', 'currency']).set_index(['date', 'accountId'])


def test_calculate_cash_flows_excludes_transfers_and_separates_currencies() -> None:
    result = _calculate_cash_flows(_transactions())

    assert result.to_dict('records') == [
        {
            'currency': 'EUR',
            'totalDeposits': 1000.0,
            'totalWithdrawals': 250.0,
            'netContributions': 750.0,
            'transactionCount': 2,
        },
        {
            'currency': 'USD',
            'totalDeposits': 300.0,
            'totalWithdrawals': 50.0,
            'netContributions': 250.0,
            'transactionCount': 2,
        },
    ]


def test_calculate_cash_flows_can_include_transfers_and_filter_account() -> None:
    result = _calculate_cash_flows(_transactions(), 'account-1', include_transfers=True)

    assert result.to_dict('records') == [{
        'currency': 'EUR',
        'totalDeposits': 1500.0,
        'totalWithdrawals': 250.0,
        'netContributions': 1250.0,
        'transactionCount': 3,
    }]


def _portfolio(names: list[str]) -> Portfolio:
    accounts = pd.DataFrame(
        [[name, AccountType.DEPOSIT.value, None, False, 'EUR'] for name in names]
        + [['Testdepot', AccountType.SECURITIES.value, None, False, 'EUR']],
        columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
        index=[f'account-{i}' for i in range(len(names))] + ['securities-1'],
    )
    accounts.index.name = 'accountId'

    return Portfolio(accounts=accounts)


def test_resolve_deposit_account_accepts_id_and_name() -> None:
    portfolio = _portfolio(['Girokonto', 'Verrechnungskonto'])

    assert _resolve_deposit_account(portfolio, 'account-1') == 'account-1'
    assert _resolve_deposit_account(portfolio, 'Girokonto') == 'account-0'


def test_resolve_deposit_account_rejects_unknown_account() -> None:
    portfolio = _portfolio(['Girokonto'])

    with pytest.raises(InputError, match="'nope' not found"):
        _resolve_deposit_account(portfolio, 'nope')


def test_resolve_deposit_account_rejects_securities_account() -> None:
    portfolio = _portfolio(['Girokonto'])

    with pytest.raises(InputError, match="'securities-1' not found"):
        _resolve_deposit_account(portfolio, 'securities-1')


def test_resolve_deposit_account_rejects_ambiguous_name() -> None:
    portfolio = _portfolio(['Girokonto', 'Girokonto'])

    with pytest.raises(InputError, match='matches multiple deposit accounts'):
        _resolve_deposit_account(portfolio, 'Girokonto')

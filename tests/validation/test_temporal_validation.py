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
# pylint: disable=too-few-public-methods,cell-var-from-loop

from datetime import datetime
import pandas as pd
import pytest

from pp_terminal.validation.rules import BalanceLimitRule


@pytest.fixture(name='entity')
def fixture_entity() -> pd.Series:
    return pd.Series({'name': 'Test Account', 'balance': 10000.0})


@pytest.fixture(name='rule_no_months')
def fixture_rule_no_months() -> BalanceLimitRule:
    return BalanceLimitRule(
        rule_type='balance-limit',
        value=5000.0,
        severity='error',
        applies_to=None,
        valid_months=None
    )


@pytest.fixture(name='rule_dec_jan')
def fixture_rule_dec_jan() -> BalanceLimitRule:
    return BalanceLimitRule(
        rule_type='balance-limit',
        value=5000.0,
        severity='error',
        applies_to=None,
        valid_months=[12, 1]
    )


def test_rule_without_valid_months_always_runs(rule_no_months: BalanceLimitRule, entity: pd.Series) -> None:
    is_error, message = rule_no_months.validate(entity, 'account-1', {'balance': 10000.0})

    assert is_error is True
    assert message is not None
    assert 'exceeds limit' in message


def test_rule_runs_in_configured_months(rule_dec_jan: BalanceLimitRule, entity: pd.Series, monkeypatch: pytest.MonkeyPatch) -> None:
    class MockDatetime:
        @staticmethod
        def now() -> datetime:
            return datetime(2025, 12, 15)

    monkeypatch.setattr('pp_terminal.validation.base.datetime', MockDatetime)
    is_error, message = rule_dec_jan.validate(entity, 'account-1', {'balance': 10000.0})
    assert is_error is True
    assert message is not None
    assert 'exceeds limit' in message

    class MockDatetime2:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 1, 15)

    monkeypatch.setattr('pp_terminal.validation.base.datetime', MockDatetime2)
    is_error, message = rule_dec_jan.validate(entity, 'account-1', {'balance': 10000.0})
    assert is_error is True
    assert message is not None
    assert 'exceeds limit' in message


def test_rule_skips_in_non_configured_months(rule_dec_jan: BalanceLimitRule, entity: pd.Series, monkeypatch: pytest.MonkeyPatch) -> None:
    for month in [2, 6, 11]:
        class MockDatetime:
            @classmethod
            def now(cls) -> datetime:
                return datetime(2026, month, 15)

        monkeypatch.setattr('pp_terminal.validation.base.datetime', MockDatetime)
        is_error, message = rule_dec_jan.validate(entity, 'account-1', {'balance': 10000.0})
        assert is_error is False
        assert message is None


def test_rule_with_single_month(entity: pd.Series, monkeypatch: pytest.MonkeyPatch) -> None:
    rule = BalanceLimitRule(
        rule_type='balance-limit',
        value=5000.0,
        severity='error',
        applies_to=None,
        valid_months=[3]
    )

    class MockDatetimeMarch:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 3, 1)

    monkeypatch.setattr('pp_terminal.validation.base.datetime', MockDatetimeMarch)
    is_error, message = rule.validate(entity, 'account-1', {'balance': 10000.0})
    assert is_error is True
    assert message is not None
    assert 'exceeds limit' in message

    class MockDatetimeApril:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 4, 1)

    monkeypatch.setattr('pp_terminal.validation.base.datetime', MockDatetimeApril)
    is_error, message = rule.validate(entity, 'account-1', {'balance': 10000.0})
    assert is_error is False
    assert message is None


def test_rule_with_empty_months_list_never_runs(entity: pd.Series, monkeypatch: pytest.MonkeyPatch) -> None:
    rule = BalanceLimitRule(
        rule_type='balance-limit',
        value=5000.0,
        severity='error',
        applies_to=None,
        valid_months=[]
    )

    for month in range(1, 13):
        class MockDatetime:
            @classmethod
            def now(cls) -> datetime:
                return datetime(2026, month, 15)

        monkeypatch.setattr('pp_terminal.validation.base.datetime', MockDatetime)
        is_error, message = rule.validate(entity, 'account-1', {'balance': 10000.0})
        assert is_error is False
        assert message is None

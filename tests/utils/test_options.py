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

from types import SimpleNamespace
from typing import Any, cast

import pytest
import typer

from pp_terminal.utils.config import load_config
from pp_terminal.utils.options import allowance_callback, exempt_rate_callback, tax_rate_callback

_PARAM = cast(typer.CallbackParam, None)


def _make_ctx(config: dict[str, Any]) -> typer.Context:
    return cast(typer.Context, SimpleNamespace(obj=SimpleNamespace(config=load_config(config))))


def _fail_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(typer, 'prompt', lambda *args, **kwargs: pytest.fail('must not prompt'))


def test_should_return_cli_tax_rate_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_prompt(monkeypatch)

    assert tax_rate_callback(_make_ctx({'tax': {'rate': 25.0}}), _PARAM, 42.0) == pytest.approx(42.0)

def test_should_fall_back_to_configured_tax_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_prompt(monkeypatch)

    assert tax_rate_callback(_make_ctx({'tax': {'rate': 25.0}}), _PARAM, None) == pytest.approx(25.0)

def test_should_prompt_for_tax_rate_when_neither_cli_nor_config_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(typer, 'prompt', lambda *args, **kwargs: 19.5)

    assert tax_rate_callback(_make_ctx({}), _PARAM, None) == pytest.approx(19.5)

def test_should_prompt_for_tax_rate_when_tax_section_lacks_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    # a [tax] section that sets only other keys must still prompt for the (defaulted) rate
    monkeypatch.setattr(typer, 'prompt', lambda *args, **kwargs: 19.5)

    assert tax_rate_callback(_make_ctx({'tax': {'allowance': 2000.0}}), _PARAM, None) == pytest.approx(19.5)

def test_should_return_cli_exempt_rate_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_prompt(monkeypatch)

    assert exempt_rate_callback(_make_ctx({'tax': {'exemption-rate': 30.0}}), _PARAM, 15.0) == pytest.approx(15.0)

def test_should_fall_back_to_configured_exempt_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_prompt(monkeypatch)

    assert exempt_rate_callback(_make_ctx({'tax': {'exemption-rate': 30.0}}), _PARAM, None) == pytest.approx(30.0)

def test_should_prompt_for_exempt_rate_when_neither_cli_nor_config_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(typer, 'prompt', lambda *args, **kwargs: 70.0)

    assert exempt_rate_callback(_make_ctx({}), _PARAM, None) == pytest.approx(70.0)

def test_should_return_cli_allowance_unchanged() -> None:
    assert allowance_callback(_make_ctx({'tax': {'allowance': 2000.0}}), _PARAM, 801.0) == pytest.approx(801.0)

def test_should_fall_back_to_configured_allowance() -> None:
    assert allowance_callback(_make_ctx({'tax': {'allowance': 2000.0}}), _PARAM, None) == pytest.approx(2000.0)

def test_should_fall_back_to_default_allowance_when_not_configured() -> None:
    assert allowance_callback(_make_ctx({}), _PARAM, None) == pytest.approx(1000.0)

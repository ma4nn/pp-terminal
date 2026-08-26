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

import importlib.metadata
import logging
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from _pytest.fixtures import TopRequest
from pydantic import Field

from pp_terminal.commands.simulate_pmt import PmtConfig
from pp_terminal.commands.view_accounts import ViewAccountsConfig
from pp_terminal.exceptions import ConfigValidationError
from pp_terminal.utils.config import (
    ConfigModel,
    build_config_model,
    command_config,
    empty_config,
    get_config,
    get_config_path,
    load_config,
    validated_toml_loader,
)

_PLUGIN_GROUP = 'pp_terminal.config_model'


class SafeWithdrawalConfig(ConfigModel):
    years: int = Field(40, ge=1)


class MonteCarloConfig(ConfigModel):
    quota: float = Field(0.5, ge=0, le=1)


NOT_A_MODEL = 'not a config model'


@pytest.fixture(autouse=True)
def _reset_model_cache() -> Iterator[None]:
    build_config_model.cache_clear()
    yield
    build_config_model.cache_clear()


def _install_models(monkeypatch: pytest.MonkeyPatch, models: dict[str, str]) -> None:
    entry_points = tuple(importlib.metadata.EntryPoint(name=name, value=value, group=_PLUGIN_GROUP) for name, value in models.items())
    monkeypatch.setattr(importlib.metadata, 'entry_points', lambda group: entry_points if group == _PLUGIN_GROUP else ())
    build_config_model.cache_clear()


def _write_config(tmp_path: Path, content: str) -> str:
    config_file = tmp_path / 'config.toml'
    config_file.write_text(content, encoding='utf-8')
    return str(config_file)


# --- path resolution (the loader still returns the raw mapping for typer-config) ---

def test_should_load_config_from_default_xdg_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: TopRequest) -> None:
    config_dir = tmp_path / 'pp-terminal'
    config_dir.mkdir()
    (config_dir / 'config.toml').write_text((request.path.parent.parent / 'fixtures' / 'minimal.toml').read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    result = validated_toml_loader('')

    assert result.get('precision') == 4
    assert result.get('tax', {}).get('rate') == pytest.approx(27.375)

def test_should_expose_path_of_loaded_default_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: TopRequest) -> None:
    """The main callback reports this path, once verbose logging is set up."""
    config_dir = tmp_path / 'pp-terminal'
    config_dir.mkdir()
    (config_dir / 'config.toml').write_text((request.path.parent.parent / 'fixtures' / 'minimal.toml').read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    validated_toml_loader('')

    assert get_config_path() == str(config_dir / 'config.toml')

def test_should_ignore_xdg_config_when_cli_config_provided(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: TopRequest) -> None:
    config_dir = tmp_path / 'pp-terminal'
    config_dir.mkdir()
    (config_dir / 'config.toml').write_text((request.path.parent.parent / 'fixtures' / 'kommer.toml').read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    result = validated_toml_loader(str(request.path.parent.parent / 'fixtures' / 'minimal.toml'))

    assert 'commands' not in result

def test_should_return_empty_config_when_no_cli_config_and_no_xdg_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    result = validated_toml_loader('')

    assert result == {}

def test_should_honor_home_fallback_when_xdg_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: TopRequest) -> None:
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    config_dir = tmp_path / '.config' / 'pp-terminal'
    config_dir.mkdir(parents=True)
    (config_dir / 'config.toml').write_text((request.path.parent.parent / 'fixtures' / 'minimal.toml').read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)

    result = validated_toml_loader('')

    assert result.get('precision') == 4


# --- typed access ---

def test_should_expose_typed_config_after_load(request: TopRequest) -> None:
    validated_toml_loader(str(request.path.parent.parent / 'fixtures' / 'kommer.toml'))

    assert get_config().tax.rate == pytest.approx(27.375)
    assert get_config().tax.exemption_rate_attribute == '2baac2d0-459b-4b41-a0ef-d7dad0866892'

def test_should_parse_native_toml_date(tmp_path: Path) -> None:
    validated_toml_loader(_write_config(tmp_path, '[commands.simulate.pmt]\nend-date = 2055-12-31\n'))

    assert command_config(get_config(), PmtConfig).end_date == date(2055, 12, 31)

def test_should_default_allowance_when_not_configured() -> None:
    assert empty_config().tax.allowance == pytest.approx(1000.0)

def test_should_default_to_no_tax_files() -> None:
    assert empty_config().tax.files == []

def test_should_coerce_single_tax_file_to_list() -> None:
    assert load_config({'tax': {'files': 'taxes.csv'}}).tax.files == [Path('taxes.csv')]

def test_should_keep_tax_file_list() -> None:
    assert load_config({'tax': {'files': ['a.csv', 'b.csv']}}).tax.files == [Path('a.csv'), Path('b.csv')]


# --- invalid config handling ---

def test_should_ignore_invalid_config_at_default_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    config_dir = tmp_path / 'pp-terminal'
    config_dir.mkdir()
    (config_dir / 'config.toml').write_text('[commands.simulate.safe-withdrawal]\nyears = 40\n', encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    with caplog.at_level(logging.WARNING):
        result = validated_toml_loader('')

    assert result == {}
    assert 'Ignoring invalid config' in caplog.text

def test_should_reject_invalid_config_when_explicitly_passed(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError, match=r'commands\.simulate\.safe-withdrawal'):
        validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 40\n'))

def test_should_reject_negative_allowance(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError, match=r'tax\.allowance'):
        validated_toml_loader(_write_config(tmp_path, '[tax]\nallowance = -1\n'))


# --- closed-world validation ---

def test_should_reject_unknown_top_level_key() -> None:
    with pytest.raises(Exception, match=r'unexpected|not permitted'):
        load_config({'nonsense': 1})

def test_should_reject_unknown_key_in_core_section() -> None:
    with pytest.raises(Exception, match=r'unexpected|not permitted'):
        load_config({'tax': {'ratee': 5}})

def test_should_reject_unknown_command_section() -> None:
    with pytest.raises(Exception, match=r'unexpected|not permitted'):
        load_config({'commands': {'simulate': {'unknown': {}}}})

def test_should_reject_malformed_uuid_in_rule_value() -> None:
    with pytest.raises(Exception, match=r'value'):
        load_config({'commands': {'validate': {'accounts': {'rules': [
            {'type': 'balance-limit-from-attribute', 'value': 'not-a-uuid'},
        ]}}}})


# --- plugin (out-of-repo) extension via entry points ---

def test_should_validate_registered_plugin_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_models(monkeypatch, {'simulate.safe-withdrawal': 'tests.utils.test_config:SafeWithdrawalConfig'})

    validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 30\n'))

    assert command_config(get_config(), SafeWithdrawalConfig).years == 30

def test_should_reject_plugin_section_when_not_registered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_models(monkeypatch, {})

    with pytest.raises(ConfigValidationError, match=r'commands\.simulate\.safe-withdrawal'):
        validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 30\n'))

def test_should_reject_unknown_key_in_plugin_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_models(monkeypatch, {'simulate.safe-withdrawal': 'tests.utils.test_config:SafeWithdrawalConfig'})

    with pytest.raises(ConfigValidationError, match=r'commands\.simulate\.safe-withdrawal\.unknown'):
        validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nunknown = 1\n'))

def test_should_mount_multiple_plugins_into_same_group(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_models(monkeypatch, {
        'simulate.safe-withdrawal': 'tests.utils.test_config:SafeWithdrawalConfig',
        'simulate.monte-carlo': 'tests.utils.test_config:MonteCarloConfig',
    })

    validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 25\n[commands.simulate.monte-carlo]\nquota = 0.3\n'))

    assert command_config(get_config(), SafeWithdrawalConfig).years == 25
    assert command_config(get_config(), MonteCarloConfig).quota == pytest.approx(0.3)

def test_should_skip_plugin_colliding_with_core_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _install_models(monkeypatch, {'view.accounts': 'tests.utils.test_config:SafeWithdrawalConfig'})

    with caplog.at_level(logging.ERROR):
        validated_toml_loader(_write_config(tmp_path, '[commands.view.accounts]\nfields = ["Name"]\n'))

    assert 'commands.view.accounts' in caplog.text
    assert command_config(get_config(), ViewAccountsConfig).fields == ['Name']  # the core section stays authoritative

def test_should_skip_plugin_that_is_not_a_config_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _install_models(monkeypatch, {
        'simulate.broken': 'tests.utils.test_config:NOT_A_MODEL',
        'simulate.safe-withdrawal': 'tests.utils.test_config:SafeWithdrawalConfig',
    })

    with caplog.at_level(logging.ERROR):
        validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 40\n'))

    assert command_config(get_config(), SafeWithdrawalConfig).years == 40
    assert 'simulate.broken' in caplog.text

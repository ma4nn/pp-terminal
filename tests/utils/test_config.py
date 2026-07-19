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
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.fixtures import TopRequest
from jsonschema import ValidationError as JsonSchemaValidationError

from pp_terminal.utils.config import validated_toml_loader, get_command_config, get_allowance, _load_schema, _merged_schema

PLUGIN_SCHEMA = {'type': 'object', 'properties': {'years': {'type': 'integer'}}, 'additionalProperties': False}
OTHER_PLUGIN_SCHEMA = {'type': 'object', 'properties': {'quota': {'type': 'number'}}, 'additionalProperties': False}
NOT_A_DICT_FRAGMENT = 'not a dict'
INVALID_SCHEMA_FRAGMENT = {'type': 'object', 'properties': {'x': {'type': 'objct'}}}
REF_FRAGMENT = {'type': 'object', 'properties': {'years': {'$ref': '#/definitions/year'}}, 'definitions': {'year': {'type': 'integer'}}}

_FRAGMENT_GROUP = 'pp_terminal.config_schema'


@pytest.fixture(autouse=True)
def _reset_schema_cache() -> Iterator[None]:
    _merged_schema.cache_clear()
    yield
    _merged_schema.cache_clear()


def _install_fragments(monkeypatch: pytest.MonkeyPatch, fragments: dict[str, str]) -> None:
    entry_points = tuple(importlib.metadata.EntryPoint(name=name, value=value, group=_FRAGMENT_GROUP) for name, value in fragments.items())
    monkeypatch.setattr(importlib.metadata, 'entry_points', lambda group: entry_points if group == _FRAGMENT_GROUP else ())


def _write_config(tmp_path: Path, content: str) -> str:
    config_file = tmp_path / 'config.toml'
    config_file.write_text(content, encoding='utf-8')
    return str(config_file)


def test_should_load_config_from_default_xdg_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: TopRequest) -> None:
    config_dir = tmp_path / 'pp-terminal'
    config_dir.mkdir()
    (config_dir / 'config.toml').write_text((request.path.parent.parent / 'fixtures' / 'minimal.toml').read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    result = validated_toml_loader('')

    assert result.get('precision') == 4
    assert result.get('tax', {}).get('rate') == pytest.approx(27.375)

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

def test_should_ignore_invalid_config_at_default_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _install_fragments(monkeypatch, {})  # no plugins -> a [commands.simulate...] section is schema-invalid
    config_dir = tmp_path / 'pp-terminal'
    config_dir.mkdir()
    (config_dir / 'config.toml').write_text('[commands.simulate.safe-withdrawal]\nyears = 40\n', encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    with caplog.at_level(logging.WARNING):
        result = validated_toml_loader('')

    assert result == {}
    assert 'Ignoring invalid config' in caplog.text

def test_should_still_reject_invalid_config_when_explicitly_passed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fragments(monkeypatch, {})

    with pytest.raises(JsonSchemaValidationError, match=r"'simulate' was unexpected"):
        validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 40\n'))

def test_should_validate_plugin_config_when_fragment_registered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fragments(monkeypatch, {'simulate.safe-withdrawal': 'tests.utils.test_config:PLUGIN_SCHEMA'})

    result = validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 40\n'))

    assert get_command_config(result, 'simulate.safe-withdrawal.years') == 40

def test_should_reject_plugin_config_when_fragment_not_registered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fragments(monkeypatch, {})

    with pytest.raises(JsonSchemaValidationError, match=r"'simulate' was unexpected"):
        validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 40\n'))

def test_should_reject_unknown_key_in_plugin_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fragments(monkeypatch, {'simulate.safe-withdrawal': 'tests.utils.test_config:PLUGIN_SCHEMA'})

    with pytest.raises(JsonSchemaValidationError, match=r"commands\.simulate\.safe-withdrawal: .*'unknown' was unexpected"):
        validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nunknown = 1\n'))

def test_should_reject_type_violation_in_plugin_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fragments(monkeypatch, {'simulate.safe-withdrawal': 'tests.utils.test_config:PLUGIN_SCHEMA'})

    with pytest.raises(JsonSchemaValidationError, match=r"commands\.simulate\.safe-withdrawal\.years:"):
        validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = "40"\n'))

def test_should_mount_multiple_fragments_into_same_group(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fragments(monkeypatch, {
        'simulate.safe-withdrawal': 'tests.utils.test_config:PLUGIN_SCHEMA',
        'simulate.monte-carlo': 'tests.utils.test_config:OTHER_PLUGIN_SCHEMA',
    })

    result = validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 40\n[commands.simulate.monte-carlo]\nquota = 0.5\n'))

    assert get_command_config(result, 'simulate.safe-withdrawal.years') == 40
    assert get_command_config(result, 'simulate.monte-carlo.quota') == pytest.approx(0.5)

def test_should_mount_fragment_into_core_group(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fragments(monkeypatch, {'view.myreport': 'tests.utils.test_config:PLUGIN_SCHEMA'})

    result = validated_toml_loader(_write_config(tmp_path, '[commands.view.accounts]\nfields = ["Name"]\n[commands.view.myreport]\nyears = 1\n'))

    assert get_command_config(result, 'view.myreport.years') == 1
    assert get_command_config(result, 'view.accounts.fields') == ['Name']

def test_should_fail_when_fragment_collides_with_core_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fragments(monkeypatch, {'view.accounts': 'tests.utils.test_config:PLUGIN_SCHEMA'})

    with pytest.raises(RuntimeError, match=r'commands\.view\.accounts'):
        validated_toml_loader(_write_config(tmp_path, 'precision = 4\n'))

def test_should_skip_broken_schema_fragments(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _install_fragments(monkeypatch, {
        'simulate.broken-import': 'nonexistent_module_xyz:SCHEMA',
        'simulate.not-a-dict': 'tests.utils.test_config:NOT_A_DICT_FRAGMENT',
        'simulate.invalid-schema': 'tests.utils.test_config:INVALID_SCHEMA_FRAGMENT',
        'simulate.with-ref': 'tests.utils.test_config:REF_FRAGMENT',
        'simulate.safe-withdrawal': 'tests.utils.test_config:PLUGIN_SCHEMA',
    })

    with caplog.at_level(logging.ERROR):
        result = validated_toml_loader(_write_config(tmp_path, '[commands.simulate.safe-withdrawal]\nyears = 40\n'))

    assert get_command_config(result, 'simulate.safe-withdrawal.years') == 40
    assert 'simulate.broken-import' in caplog.text
    assert 'simulate.not-a-dict' in caplog.text
    assert 'simulate.invalid-schema' in caplog.text
    assert 'simulate.with-ref' in caplog.text

def test_should_reject_mounting_inside_another_plugins_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mount path nested inside an earlier fragment is a name conflict, and the fragment constant must stay pristine."""
    _install_fragments(monkeypatch, {
        'simulate': 'tests.utils.test_config:PLUGIN_SCHEMA',
        'simulate.foo': 'tests.utils.test_config:OTHER_PLUGIN_SCHEMA',
    })
    pristine = json.dumps(PLUGIN_SCHEMA, sort_keys=True)

    with pytest.raises(RuntimeError, match=r'commands\.simulate\.foo'):
        _merged_schema()

    assert json.dumps(PLUGIN_SCHEMA, sort_keys=True) == pristine

def test_should_skip_fragment_with_too_deep_command_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Entry point names have at most two segments, so a core leaf section can never be extended from within."""
    _install_fragments(monkeypatch, {'view.accounts.evilchild': 'tests.utils.test_config:PLUGIN_SCHEMA'})

    with caplog.at_level(logging.ERROR):
        result = validated_toml_loader(_write_config(tmp_path, '[commands.view.accounts]\nfields = ["Name"]\n'))

    assert get_command_config(result, 'view.accounts.fields') == ['Name']
    assert 'view.accounts.evilchild' in caplog.text

    with pytest.raises(JsonSchemaValidationError, match=r"'evilchild' was unexpected"):
        validated_toml_loader(_write_config(tmp_path, '[commands.view.accounts.evilchild]\nyears = 1\n'))

def test_should_keep_schema_unchanged_without_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fragments(monkeypatch, {})

    assert _merged_schema() == _load_schema()

def test_should_return_default_allowance_when_not_configured() -> None:
    assert get_allowance({}) == pytest.approx(1000.0)

def test_should_return_configured_allowance(tmp_path: Path) -> None:
    result = validated_toml_loader(_write_config(tmp_path, '[tax]\nallowance = 2000\n'))

    assert get_allowance(result) == pytest.approx(2000.0)

def test_should_reject_negative_allowance(tmp_path: Path) -> None:
    with pytest.raises(JsonSchemaValidationError, match=r'tax\.allowance'):
        validated_toml_loader(_write_config(tmp_path, '[tax]\nallowance = -1\n'))

def test_should_merge_fragments_deterministically_regardless_of_registration_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fragments = {
        'simulate.safe-withdrawal': 'tests.utils.test_config:PLUGIN_SCHEMA',
        'simulate.monte-carlo': 'tests.utils.test_config:OTHER_PLUGIN_SCHEMA',
    }
    _install_fragments(monkeypatch, fragments)
    first = json.dumps(_merged_schema())

    _merged_schema.cache_clear()
    _install_fragments(monkeypatch, dict(reversed(list(fragments.items()))))
    second = json.dumps(_merged_schema())

    assert first == second

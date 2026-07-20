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

import pytest
import typer

from pp_terminal.utils.plugins import load_command_plugins

PLUGIN_APP = typer.Typer()
NOT_A_TYPER = {'not': 'a typer app'}

_PLUGIN_GROUP = 'pp_terminal.commands'


def _install_plugins(monkeypatch: pytest.MonkeyPatch, plugins: dict[str, str]) -> None:
    entry_points = tuple(importlib.metadata.EntryPoint(name=name, value=value, group=_PLUGIN_GROUP) for name, value in plugins.items())
    monkeypatch.setattr(importlib.metadata, 'entry_points', lambda group: entry_points if group == _PLUGIN_GROUP else ())


def _make_app_with_group(group_name: str) -> tuple[typer.Typer, typer.Typer]:
    app = typer.Typer()
    group = typer.Typer()
    app.add_typer(group, name=group_name)
    return app, group


def _loaded_plugins(app: typer.Typer) -> list[typer.Typer | None]:
    return [group.typer_instance for group in app.registered_groups]


def test_should_load_plugin_into_matching_group(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_plugins(monkeypatch, {'simulate.myplugin': 'tests.utils.test_plugins:PLUGIN_APP'})
    app, group = _make_app_with_group('simulate')

    load_command_plugins(app)

    assert PLUGIN_APP in _loaded_plugins(group)
    assert PLUGIN_APP not in _loaded_plugins(app)

def test_should_load_bare_named_plugin_into_top_level_app(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_plugins(monkeypatch, {'standalone': 'tests.utils.test_plugins:PLUGIN_APP'})
    app, group = _make_app_with_group('simulate')

    load_command_plugins(app)

    assert PLUGIN_APP in _loaded_plugins(app)
    assert PLUGIN_APP not in _loaded_plugins(group)

def test_should_load_plugin_with_unknown_group_into_top_level_app(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_plugins(monkeypatch, {'unknown.myplugin': 'tests.utils.test_plugins:PLUGIN_APP'})
    app, group = _make_app_with_group('simulate')

    load_command_plugins(app)

    assert PLUGIN_APP in _loaded_plugins(app)
    assert PLUGIN_APP not in _loaded_plugins(group)

def test_should_skip_broken_plugins_and_still_load_valid_one(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _install_plugins(monkeypatch, {
        'simulate.broken-import': 'nonexistent_module_xyz:app',
        'simulate.not-a-typer': 'tests.utils.test_plugins:NOT_A_TYPER',
        'simulate.valid': 'tests.utils.test_plugins:PLUGIN_APP',
    })
    app, group = _make_app_with_group('simulate')

    with caplog.at_level(logging.ERROR):
        load_command_plugins(app)

    assert _loaded_plugins(group) == [PLUGIN_APP]
    assert 'simulate.broken-import' in caplog.text
    assert 'simulate.not-a-typer' in caplog.text
    assert 'not a Typer app' in caplog.text

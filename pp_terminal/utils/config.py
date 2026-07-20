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
import os
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Mapping, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, create_model, field_validator
from typer_config import conf_callback_factory
from typer_config.loaders import toml_loader

from pp_terminal.exceptions import ConfigValidationError

log = logging.getLogger(__name__)

_PLUGIN_GROUP = 'pp_terminal.config_model'

UUIDStr = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]


def _to_kebab(name: str) -> str:
    return name.replace('_', '-')


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra='forbid', alias_generator=_to_kebab, populate_by_name=True)


class AttributeAnonymization(ConfigModel):
    provider: str
    args: dict[str, Any] = {}


class AnonymizeConfig(ConfigModel):
    attributes: dict[str, AttributeAnonymization] = {}


class TaxConfig(ConfigModel):
    rate: float = Field(26.375, ge=0, le=100)
    files: list[Path] = []
    exemption_rate: float = Field(30.0, ge=0, le=100)
    exemption_rate_attribute: UUIDStr | None = None
    allowance: float = Field(1000.0, ge=0)

    @field_validator('files', mode='before')
    @classmethod
    def _coerce_files(cls, value: Any) -> Any:
        # a single path may be given without wrapping it in a list
        return [value] if isinstance(value, str) else value


class AppConfig(ConfigModel):
    file: str | None = None
    precision: int | None = Field(None, ge=0)
    taxonomy: str | None = None
    anonymize: AnonymizeConfig | None = None
    tax: TaxConfig = Field(default_factory=TaxConfig)


Config = AppConfig

_ConfigT = TypeVar('_ConfigT', bound=ConfigModel)

# python-identifier path (below "commands") of each registered command config model
_COMMAND_PATHS: dict[type[ConfigModel], tuple[str, ...]] = {}

# Global storage for the loaded config
_loaded_config: AppConfig | None = None  # pylint: disable=invalid-name


class _MountConflict(Exception):
    pass


def _core_command_models() -> dict[str, type[ConfigModel]]:
    from pp_terminal.commands.simulate_pmt import PmtConfig  # pylint: disable=import-outside-toplevel,cyclic-import
    from pp_terminal.commands.view_accounts import ViewAccountsConfig  # pylint: disable=import-outside-toplevel,cyclic-import
    from pp_terminal.commands.view_securities import ViewSecuritiesConfig  # pylint: disable=import-outside-toplevel,cyclic-import
    from pp_terminal.validation.rules import ValidateConfig  # pylint: disable=import-outside-toplevel,cyclic-import

    return {
        'simulate.pmt': PmtConfig,
        'validate': ValidateConfig,
        'view.accounts': ViewAccountsConfig,
        'view.securities': ViewSecuritiesConfig,
    }


def _discover_plugin_models() -> Iterator[tuple[str, type[ConfigModel]]]:
    entry_points = sorted(importlib.metadata.entry_points(group=_PLUGIN_GROUP), key=lambda ep: ep.name)
    for entry_point in entry_points:
        try:
            model = entry_point.load()
            if not (isinstance(model, type) and issubclass(model, ConfigModel)):
                raise TypeError('not a ConfigModel subclass')
        except Exception as e:  # pylint: disable=broad-exception-caught
            log.error("failed to load config model %s, ignoring: %s", entry_point.name, e)
            continue
        yield entry_point.name, model


def _mount(tree: dict[str, Any], path: str, model: type[ConfigModel]) -> None:
    segments = path.split('.')
    node = tree
    for segment in segments[:-1]:
        child = node.setdefault(segment, {})
        if not isinstance(child, dict):
            raise _MountConflict(path)
        node = child
    leaf = segments[-1]
    if leaf in node:
        raise _MountConflict(path)
    node[leaf] = model


def _model_from_tree(name: str, tree: dict[str, Any], prefix: tuple[str, ...]) -> type[ConfigModel]:
    fields: dict[str, Any] = {}
    for segment, value in tree.items():
        ident = segment.replace('-', '_')
        if ident in dir(BaseModel):  # a command named e.g. "validate" would shadow a BaseModel member
            ident += '_'
        path = prefix + (ident,)
        if isinstance(value, dict):
            submodel: type[ConfigModel] = _model_from_tree(f'{name}_{ident}', value, path)
        else:
            submodel = value
            _COMMAND_PATHS[submodel] = path
        fields[ident] = (submodel, Field(default_factory=submodel, alias=segment))
    return cast('type[ConfigModel]', create_model(name, __config__=ConfigModel.model_config, **fields))


@cache
def build_config_model() -> type[AppConfig]:
    _COMMAND_PATHS.clear()
    tree: dict[str, Any] = {}
    for path, model in _core_command_models().items():
        _mount(tree, path, model)
    for path, model in _discover_plugin_models():
        try:
            _mount(tree, path, model)
        except _MountConflict:
            log.error("config model for commands.%s conflicts with an existing section, ignoring", path)

    commands_model = _model_from_tree('Commands', tree, ())
    return cast('type[AppConfig]', create_model('LoadedConfig', __base__=AppConfig, commands=(commands_model, Field(default_factory=commands_model))))


def empty_config() -> AppConfig:
    return build_config_model()()


def load_config(data: Mapping[str, Any]) -> AppConfig:
    return build_config_model().model_validate(dict(data))


def command_config(config: AppConfig, model: type[_ConfigT]) -> _ConfigT:
    node: Any = getattr(config, 'commands')
    for ident in _COMMAND_PATHS[model]:
        node = getattr(node, ident)
    return cast('_ConfigT', node)


def _default_config_path() -> Path:
    xdg = os.environ.get('XDG_CONFIG_HOME')
    base = Path(xdg) if xdg else Path.home() / '.config'
    return base / 'pp-terminal' / 'config.toml'


def _format_errors(config_path: str, exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = '.'.join(str(part) for part in error['loc']) if error['loc'] else 'root'
        lines.append(f"  {location}: {error['msg']}")
    return f"Config validation failed for {config_path}:\n" + '\n'.join(sorted(lines))


def validated_toml_loader(config_path: str) -> dict[str, Any]:
    """
    Load and validate a TOML configuration file for use with typer-config.

    Validates into the typed AppConfig (stored for get_config()) and returns the
    raw mapping so typer-config can map top-level keys onto CLI option defaults.
    """
    global _loaded_config  # pylint: disable=global-statement

    explicit_config = config_path != ''
    if config_path == '':
        default_path = _default_config_path()
        if default_path.is_file():
            config_path = str(default_path)

    if config_path == '':
        return {}

    raw = toml_loader(config_path)

    try:
        _loaded_config = build_config_model().model_validate(raw)
    except ValidationError as e:
        message = _format_errors(config_path, e)
        if explicit_config:
            raise ConfigValidationError(message) from e
        # a config the user did not explicitly request must never break every command
        log.warning("Ignoring invalid config at default location:\n%s", message)
        return {}

    log.debug("Loaded and validated config from file \"%s\"", config_path)

    # Presence-based anonymization: an existing [anonymize] section maps to the boolean CLI option
    if _loaded_config.anonymize is not None:
        return {**raw, 'anonymize': True}

    return raw


def get_config() -> AppConfig:
    """
    Get the currently loaded configuration - should only be used with prior @use_config(validated_config_callback).
    """
    global _loaded_config  # pylint: disable=global-statement
    if _loaded_config is None:
        _loaded_config = empty_config()
    return _loaded_config


# Create the config callback for use with @use_config decorator
validated_config_callback = conf_callback_factory(validated_toml_loader)

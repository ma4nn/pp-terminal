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

from typing import Any

import pandas as pd

from pp_terminal.commands.message_column import messages_renderer
from pp_terminal.output.strategy import RichOutputStrategy, JsonOutputStrategy
from pp_terminal.validation.base import ValidationRule
from pp_terminal.validation.engine import ValidationResult


class _StubRule(ValidationRule):
    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:  # pylint: disable=unused-argument
        return False, None


def _result(entity_id: str, message: str, severity: str) -> ValidationResult:
    return ValidationResult(entity_id=entity_id, violations=[(_StubRule('stub', None, severity=severity), message)])


def test_renders_error_and_warning_severity_via_output() -> None:
    results = {
        'sec-err': _result('sec-err', 'too high', 'error'),
        'sec-warn': _result('sec-warn', 'stale price', 'warning'),
    }
    render = messages_renderer(results, RichOutputStrategy())

    assert render('sec-err') == '[red]❌ too high[/red]'
    assert render('sec-warn') == '[yellow]⚠️ stale price[/yellow]'


def test_missing_entity_renders_empty() -> None:
    render = messages_renderer({}, RichOutputStrategy())

    assert render('unknown') == ''


def test_json_output_carries_structured_severity() -> None:
    results = {'sec-err': _result('sec-err', 'too high', 'error')}
    render = messages_renderer(results, JsonOutputStrategy())

    assert render('sec-err') == {'severity': 'error', 'text': 'too high'}

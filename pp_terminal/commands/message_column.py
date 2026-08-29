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

from typing import Any, Callable

from pp_terminal.output.strategy import OutputStrategy
from pp_terminal.validation.engine import ValidationResult


def messages_renderer(
    validation_results: dict[str, ValidationResult],
    output: OutputStrategy
) -> Callable[[Any], Any]:
    """Returns a mapper turning an entity id into its rendered validation messages.

    The rendered value is format-specific: a display string for table/CSV, a
    structured severity object for JSON. Hence the ``Any`` return type.
    """
    def render(entity_id: Any) -> Any:
        result = validation_results.get(str(entity_id), ValidationResult.empty())
        return output.render_messages(result.violation_messages, result.has_errors)
    return render

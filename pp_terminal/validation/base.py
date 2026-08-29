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

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, TYPE_CHECKING
import logging
import pandas as pd

if TYPE_CHECKING:
    from pp_terminal.domain.portfolio import Portfolio
    from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
    from pp_terminal.utils.config import Config

log = logging.getLogger(__name__)


class ValidationRule(ABC):
    def __init__(  # pylint: disable=too-many-arguments
        self,
        rule_type: str,
        value: Any,
        severity: str = 'error',
        applies_to: list[str] | None = None,
        *,
        valid_months: list[int] | None = None,
        tolerance: float = 0.0
    ):
        self.rule_type = rule_type
        self._value = value
        self.severity = severity
        self.applies_to = applies_to
        self.valid_months = valid_months
        self.tolerance = tolerance

    @classmethod
    def provide_context(cls, portfolio: 'Portfolio', snapshot: 'PortfolioSnapshot', config: 'Config') -> dict[str, Any]:  # pylint: disable=unused-argument
        """Override to contribute data to shared validation context."""
        return {}

    def _should_apply(self, current_date: datetime | None = None) -> bool:
        if current_date is None:
            current_date = datetime.now()

        if self.valid_months is not None:
            current_month = current_date.month
            if current_month not in self.valid_months:
                log.debug('%s skipped: current month %d not in valid-months %s', str(self), current_month, self.valid_months)
                return False

        return True

    @abstractmethod
    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:
        """
        Validate entity and return (is_error, message) tuple.

        Returns:
            tuple[bool, str | None]:
                - First element: True if error occurred (severity='error' and validation failed)
                - Second element: Violation message if validation failed, None otherwise
        """
        if not self._should_apply():
            return False, None

        log.debug(
            'Validating %s of "%s" (%s) using value %s %s',
            str(self),
            entity["name"],
            entity_id,
            str(self._get_value(entity)),
            '(' + str(self._value) + ')' if self._value != self._get_value(entity) else '')

        return False, None

    def matches_entity(self, entity: pd.Series, entity_id: str) -> bool:
        if self.rule_type.endswith('-from-attribute'):
            attr_uuid = self._value
            return attr_uuid in entity.index and pd.notna(entity.get(attr_uuid))

        if self.applies_to is not None:
            return entity_id in self.applies_to

        return True

    def _get_value(self, entity: pd.Series) -> Any:
        if self.rule_type.endswith('-from-attribute'):
            attr_uuid = self._value
            return entity.get(attr_uuid)
        return self._value

    def log_violation(self, message: str) -> None:
        if self.severity == 'error':
            log.error(message)
        else:
            log.warning(message)

    def is_error(self) -> bool:
        return bool(self.severity == 'error')

    def __str__(self) -> str:
        return self.rule_type

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

import math
from datetime import datetime
from typing import Any, cast
import logging
import pandas as pd

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.vap import calculate_vap_by_account
from pp_terminal.utils.config import Config, empty_config
from pp_terminal.validation.base import ValidationRule

log = logging.getLogger(__name__)


class VapLiquidityRule(ValidationRule):
    """Validates that deposit account has sufficient balance to cover VAP liability."""

    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:  # pylint: disable=too-many-locals
        is_error, message = super().validate(entity, entity_id, context)
        if not self._should_apply():
            return is_error, message

        now = datetime.now()
        vap_year = now.year if now.month == 12 else now.year - 1

        portfolio = cast(Portfolio, context.get('portfolio'))
        config = cast(Config, context.get('config') or empty_config())
        balance = context.get('balance', 0.0)

        if not portfolio:
            log.debug('VAP liquidity check skipped: no portfolio in context')
            return False, None

        vap_totals = calculate_vap_by_account(
            portfolio,
            vap_year,
            config.tax.rate,
            config.tax.exemption_rate,
            config.tax.exemption_rate_attribute
        )

        if vap_totals is None:
            log.debug('VAP liquidity check skipped for account %s: no VAP calculated', entity_id)
            return False, None

        vap_liability = vap_totals.get(entity_id, 0.0)
        if math.isclose(vap_liability, 0.0):
            log.debug('VAP liquidity check skipped for account %s: no VAP liability', entity_id)
            return False, None

        if balance < vap_liability:
            currency = entity.get('currency', 'EUR')
            message = f'insufficient balance ({balance:.2f} {currency}) to cover estimated VAP liability ({vap_liability:.2f} {currency}) for {vap_year}'
            return self.is_error(), message

        return False, None

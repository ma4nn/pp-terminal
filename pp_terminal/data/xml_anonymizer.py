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

from pathlib import Path
import random
from datetime import datetime, timedelta
import logging
from typing import Any

import lxml.etree as ET  # pylint: disable=c-extension-no-member
from faker import Faker

log = logging.getLogger(__name__)


class XmlAnonymizer:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Anonymizes Portfolio Performance XML files."""

    # Tags that should not be anonymized
    SKIP_TAGS = {
        'uuid', 'isin', 'wkn', 'tickerSymbol', 'feed', 'feedURL',
        'currencyCode', 'targetCurrencyCode', 'calendar', 'type',
        'isRetired', 'updatedAt', 'version', 'baseCurrency'
    }

    # Both legs of a cross entry (a buy/sell pair, a depot transfer) must keep booking the same amount
    # and share count, and PP serializes them under differing tags — so they all share one factor.
    TRANSACTION_TAGS = {
        'portfolio-transaction', 'account-transaction',
        'portfolioTransaction', 'accountTransaction',
        'transactionFrom', 'transactionTo',
    }

    def __init__(
        self,
        seed: int,
        config: dict[str, Any] | None = None,
        date_shift_days_range: tuple[int, int] = (-3650, -365),
        amount_factor_range: tuple[float, float] = (0.5, 2.0)
    ):
        self.seed = seed
        self.date_shift_days_range = date_shift_days_range
        self.amount_factor_range = amount_factor_range

        self.rng = random.Random(seed)  # nosec B311  # Used for data anonymization, not cryptography
        self.faker = Faker()
        Faker.seed(seed)

        self.config = config if config is not None else {}

        # Caches for consistent anonymization
        self.amount_factors: dict[str, float] = {}
        self.date_offset: int | None = None

    def anonymize_file(self, input_path: Path, output_path: Path) -> None:
        parser = ET.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True)  # pylint: disable=c-extension-no-member
        tree = ET.parse(str(input_path), parser)  # pylint: disable=c-extension-no-member
        root = tree.getroot()

        self._init_date_offset()
        self._anonymize_element(root)

        tree.write(
            str(output_path),
            encoding='UTF-8',
            xml_declaration=True,
            pretty_print=False
        )

    def _init_date_offset(self) -> None:
        """Initialize random date offset."""
        min_days, max_days = self.date_shift_days_range
        self.date_offset = self.rng.randint(min_days, max_days)

    def _anonymize_element(self, element: ET.Element) -> None:  # pylint: disable=too-many-branches,c-extension-no-member
        """Recursively anonymize XML element and children."""
        tag = element.tag

        if tag in self.SKIP_TAGS:
            # Still process children
            for child in element:
                self._anonymize_element(child)
            return

        if tag == 'name':
            element.text = self._generate_name(element)
        elif tag in ('note', 'details'):
            element.text = self.faker.sentence() if element.text else None
        elif tag == 'date':
            element.text = self._shift_date(element.text)
        elif tag in ('amount', 'forex'):
            if element.text:
                element.text = self._randomize_amount(element.text, element)
            # Also handle amount attribute for multi-currency amounts
            if 'amount' in element.attrib:
                element.attrib['amount'] = self._randomize_amount(
                    element.attrib['amount'], element
                )
        elif tag == 'shares':
            element.text = self._randomize_amount(element.text, element)
        elif tag == 'price':
            # Price element has 't' (date) and 'v' (value) attributes
            if 't' in element.attrib:
                element.attrib['t'] = self._shift_date(element.attrib['t'])
            if 'v' in element.attrib:
                element.attrib['v'] = self._randomize_amount(
                    element.attrib['v'], element
                )
        elif tag == 'source':
            element.text = self.faker.file_name(extension="pdf") if element.text else None
        elif tag == 'entry' and element.getparent() is not None and element.getparent().tag == 'map':
            self._anonymize_attribute_entry(element)

        for child in element:
            self._anonymize_element(child)

    def _generate_name(self, element: ET.Element) -> str:  # pylint: disable=c-extension-no-member
        if not element.text or not element.text.strip():
            return ''

        parent = element.getparent()
        if parent is None:
            return self.faker.company()

        parent_tag = parent.tag
        if 'account' in parent_tag.lower():
            return f"{self.faker.word().capitalize()} Account"
        if 'portfolio' in parent_tag.lower():
            return f"{self.faker.word().capitalize()} Portfolio"

        return str(element.text)

    def _shift_date(self, date_str: str | None) -> str:
        """Shift date by random offset."""
        if not date_str or not self.date_offset:
            return ''

        try:
            if 'T' in date_str:
                # ISO format with time: "2019-01-01T00:00"
                # Handle optional Z suffix
                clean_date = date_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(clean_date)
                shifted = dt + timedelta(days=self.date_offset)
                # Preserve original format
                if date_str.endswith('Z'):
                    return shifted.isoformat().replace('+00:00', 'Z')
                return shifted.isoformat()

            # Date only: "2019-01-01"
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            shifted = dt + timedelta(days=self.date_offset)
            return shifted.strftime('%Y-%m-%d')
        except (ValueError, AttributeError):
            log.warning("Cannot parse date '%s', keeping original value", date_str)
            return date_str

    def _randomize_amount(self, amount_str: str | None, element: ET.Element) -> str:  # pylint: disable=c-extension-no-member
        """Randomize amount while preserving order of magnitude."""
        if not amount_str:
            return ''

        try:
            # Build context key for consistency
            parent = element.getparent()
            parent_tag = parent.tag if parent is not None else ''
            context = f"{element.tag}_{'transaction' if parent_tag in self.TRANSACTION_TAGS else parent_tag}"

            # Get or generate factor
            if context not in self.amount_factors:
                min_f, max_f = self.amount_factor_range
                self.amount_factors[context] = self.rng.uniform(min_f, max_f)

            factor = self.amount_factors[context]
            value = int(amount_str)
            new_value = int(value * factor)

            return str(new_value)
        except (ValueError, AttributeError):
            log.warning("Cannot parse amount '%s', keeping original value", amount_str)
            return amount_str

    def _anonymize_attribute_entry(self, entry: ET.Element) -> None:  # pylint: disable=c-extension-no-member
        strings = entry.findall('string')
        if len(strings) < 2:
            return

        attr_type_uuid = strings[0].text
        current_value = strings[1].text

        if not attr_type_uuid or not current_value:
            return

        if attr_type_uuid in self.config:
            config = self.config[attr_type_uuid]
            provider = config.get('provider')
            args = config.get('args', {})

            if provider:
                new_value = self._call_faker_provider(provider, args)
                if new_value is not None:
                    strings[1].text = new_value

    def _call_faker_provider(self, provider: str, args: dict[str, Any]) -> str | None:
        method = getattr(self.faker, provider)
        return str(method(**args))

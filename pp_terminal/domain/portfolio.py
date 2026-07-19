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

import logging

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError
from pandera.typing import DataFrame

from .schemas import AccountType, TransactionSchema, AccountSchema, SecuritySchema, SecurityPriceSchema, \
    Security, Attribute, Taxonomy
from ..exceptions import InputError

log = logging.getLogger(__name__)


class Portfolio:  # pylint: disable=too-many-instance-attributes
    _accounts: DataFrame[AccountSchema] = AccountSchema.empty()
    _securities: DataFrame[SecuritySchema] = SecuritySchema.empty()
    _transactions: DataFrame[TransactionSchema] = TransactionSchema.empty()
    _prices: DataFrame[SecurityPriceSchema] = SecurityPriceSchema.empty()
    _attributes: dict[str, dict[str, Attribute]] = {}
    _taxonomies: dict[str, Taxonomy] = {}
    _taxonomy_assignments: pd.DataFrame = pd.DataFrame()
    base_currency: str = ''

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
            self,
            accounts: DataFrame[AccountSchema] | None = None,
            transactions: DataFrame[TransactionSchema] | None = None,
            securities: DataFrame[SecuritySchema] | None = None,
            prices: DataFrame[SecurityPriceSchema] | None = None,
            attributes: dict[str, dict[str, Attribute]] | None = None,
            taxonomies: dict[str, Taxonomy] | None = None,
            taxonomy_assignments: pd.DataFrame | None = None
    ):
        if accounts is not None:
            try:
                self._accounts = AccountSchema.validate(accounts)
            except SchemaError as e:
                log.error('accounts schema invalid: %s', e)

        if securities is not None:
            try:
                self._securities = SecuritySchema.validate(securities)
            except SchemaError as e:
                log.error('securities schema invalid: %s', e)

        if transactions is not None:
            try:
                self._transactions = TransactionSchema.validate(transactions)
            except SchemaError as e:
                log.error('transactions schema invalid: %s', e)

        if prices is not None:
            try:
                self._prices = SecurityPriceSchema.validate(prices)
            except SchemaError as e:
                log.error('security prices schema invalid: %s', e)

        self._attributes = attributes if attributes is not None else {}
        self._taxonomies = taxonomies if taxonomies is not None else {}
        self._taxonomy_assignments = taxonomy_assignments if taxonomy_assignments is not None else pd.DataFrame()

    @property
    def securities_accounts(self) -> DataFrame[AccountSchema]:
        return AccountSchema.validate(self._accounts[self._accounts['type'] == AccountType.SECURITIES.value])

    @property
    def deposit_accounts(self) -> DataFrame[AccountSchema]:
        return AccountSchema.validate(self._accounts[self._accounts['type'] == AccountType.DEPOSIT.value])

    @property
    def securities_account_transactions(self) -> DataFrame[TransactionSchema]:
        return TransactionSchema.validate(self._transactions[self._transactions['accountType'] == AccountType.SECURITIES.value].sort_values(by=['date']))

    @property
    def deposit_account_transactions(self) -> DataFrame[TransactionSchema]:
        return TransactionSchema.validate(self._transactions[self._transactions['accountType'] == AccountType.DEPOSIT.value].sort_values(by=['date']))

    @property
    @pa.check_types
    def securities(self) -> DataFrame[SecuritySchema]:
        return self._securities

    @property
    @pa.check_types
    def prices(self) -> DataFrame[SecurityPriceSchema]:
        return self._prices

    @property
    def all_attributes(self) -> dict[str, str]:
        return {uuid: attr.name for attributes in self._attributes.values() for uuid, attr in attributes.items()}

    @property
    def security_attributes(self) -> dict[str, Attribute]:
        return self._attributes.get('securities', {})

    @property
    def account_attributes(self) -> dict[str, Attribute]:
        return self._attributes.get('accounts', {})

    @property
    def taxonomies(self) -> dict[str, Taxonomy]:
        return self._taxonomies

    @property
    def taxonomy_assignments(self) -> pd.DataFrame:
        return self._taxonomy_assignments


def get_security_by_id(portfolio: Portfolio, security_id: str) -> Security:
    if security_id not in portfolio.securities.index:
        raise InputError(f"Security '{security_id}' not found in portfolio")

    security_data = portfolio.securities.reset_index().set_index('securityId', drop=False).loc[security_id].to_dict()
    # Replace NaN with None for optional fields
    security_data = {k: (None if pd.isna(v) else v) for k, v in security_data.items()}
    return Security(**security_data)

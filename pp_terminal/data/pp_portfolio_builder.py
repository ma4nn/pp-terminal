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
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
from pandera.typing import DataFrame

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import TransactionSchema, AccountSchema, SecuritySchema, SecurityPriceSchema, TransactionType, Attribute, Taxonomy
from pp_terminal.exceptions import InputError
from pp_terminal.utils.cache import cleanup_old_cache_files, get_cache_path
from pp_terminal.utils.helper import enum_list_to_values
from .attribute_type_converter import convert_attribute_types, get_converter_column_name
from .ppxml2db_wrapper import Ppxml2dbWrapper, DB_NAME_IN_MEMORY

log = logging.getLogger(__name__)

_SCALE = 100000000
_CENTS_PER_EURO = 100
_NEGATIVE_DEPOSIT_ACCOUNT_TRANSACTION_TYPES = [
    TransactionType.TRANSFER_OUT,
    TransactionType.REMOVAL,
    TransactionType.INTEREST_CHARGE,
    TransactionType.FEES,
    TransactionType.TAXES,
    TransactionType.BUY,
]
_ATTRIBUTE_TYPE_ACCOUNT = 'name.abuchen.portfolio.model.Account'
_ATTRIBUTE_TYPE_SECURITY = 'name.abuchen.portfolio.model.Security'


class PpPortfolioBuilder:  # pylint: disable=too-few-public-methods
    _db: Ppxml2dbWrapper
    _config: Dict[str, Any]

    def __init__(self, db: Ppxml2dbWrapper | None = None, config: Dict[str, Any] | None = None):
        self._db = db if db is not None else Ppxml2dbWrapper(dbname=DB_NAME_IN_MEMORY)
        self._config = config if config is not None else {}

    def construct(self, file: Path) -> Portfolio:
        self._db.open(file)

        securities_attrs = self._get_attributes(_ATTRIBUTE_TYPE_SECURITY)
        account_attrs = self._get_attributes(_ATTRIBUTE_TYPE_ACCOUNT)
        taxonomies, taxonomy_assignments = self._parse_taxonomies()

        portfolio = Portfolio(
            accounts = self._parse_accounts(account_attrs),
            transactions = self._parse_transactions(),
            securities = self._parse_securities(securities_attrs),
            prices = self._parse_prices(),
            attributes = {
                'accounts': account_attrs,
                'securities': securities_attrs
            },
            taxonomies = taxonomies,
            taxonomy_assignments = taxonomy_assignments
        )

        base_currency = self._get_property('baseCurrency')

        self._db.close()

        if base_currency is None:
            raise InputError('missing baseCurrency property in the Portfolio Performance xml file "' + file.name + '"')

        portfolio.base_currency = base_currency

        return portfolio

    def _parse_securities(self, security_attrs: dict[str, Attribute]) -> DataFrame[SecuritySchema]:
        # Build dynamic SQL with CASE statements for each attribute
        case_statements = []
        params = []
        for attr_uuid in security_attrs:
            case_statements.append(f'MAX(CASE WHEN at.id = ? THEN sa.value END) AS "{attr_uuid}"')
            params.append(attr_uuid)
            case_statements.append(f'MAX(CASE WHEN at.id = ? THEN at.converterClass END) AS "{get_converter_column_name(attr_uuid)}"')
            params.append(attr_uuid)

        # B608 false positive: case_statements contain only column names; values are bound via ? placeholders
        sql = f"""
            select s.*,
                {', '.join(case_statements) if case_statements else '""'}
            from security as s
            left join security_attr as sa on sa."security" = s.uuid
            left join attribute_type as at on sa.attr_uuid = at.id
            group by s.uuid
        """  # nosec

        securities = pd.read_sql_query(sql, self._db.connection, index_col='uuid', params=params).rename_axis('securityId')
        securities = convert_attribute_types(securities, security_attrs)

        return SecuritySchema.validate(securities)

    def _parse_prices(self) -> DataFrame[SecurityPriceSchema]:
        prices = (pd.read_sql_query('select datetime(tstamp) as date, * from price', self._db.connection, index_col=['date', 'security'], parse_dates={"date": "%Y-%m-%d %H:%M:%S"}, dtype={'value': np.float64})
                          .rename(columns={'value': 'price'}))[['price']]
        prices['price'] = prices['price'] / _SCALE
        prices.index.set_names(['date', 'securityId'], inplace=True)

        return SecurityPriceSchema.validate(prices)

    def _parse_transactions(self) -> DataFrame[TransactionSchema]:
        transactions = (pd.read_sql_query("""
select datetime(x.date) as date, ifnull(xu.forex_currency, x.currency) as currency, ifnull(xu.forex_amount, x.amount)-x.fees as amount_wo_fees, x.fees, x.taxes, x.uuid, x.account, x.type, x.security, x.shares, x.acctype
from xact as x
left join xact_unit as xu on xu.xact = x.uuid and xu.type = 'GROSS_VALUE'
        """, self._db.connection, index_col=['date', 'account', 'security'], parse_dates={"date": "%Y-%m-%d %H:%M:%S"}, dtype={'amount_wo_fees': np.float64, 'shares': np.float64, 'taxes': np.float64})
                          .rename(columns={'amount_wo_fees': 'amount', 'acctype': 'accountType'}))
        transactions['shares'] = transactions['shares'] / _SCALE
        transactions['type'] = pd.Categorical(transactions['type'])
        transactions['amount'] = transactions.apply(
            lambda row: -1 if row['type'] in enum_list_to_values(_NEGATIVE_DEPOSIT_ACCOUNT_TRANSACTION_TYPES) else 1,
            axis=1
        ) * transactions['amount'] / _CENTS_PER_EURO
        transactions['taxes'] = transactions['taxes'] / _CENTS_PER_EURO
        transactions['fees'] = transactions['fees'] / _CENTS_PER_EURO
        transactions.index.set_names(['date', 'accountId', 'securityId'], inplace=True)

        return TransactionSchema.validate(transactions)

    def _parse_accounts(self, account_attrs: dict[str, Attribute]) -> DataFrame[AccountSchema]:
        # Build dynamic SQL with CASE statements for each attribute
        case_statements = []
        params = []
        for attr_uuid in account_attrs:
            case_statements.append(f'MAX(CASE WHEN at.id = ? THEN aa.value END) AS "{attr_uuid}"')
            params.append(attr_uuid)
            case_statements.append(f'MAX(CASE WHEN at.id = ? THEN at.converterClass END) AS "{get_converter_column_name(attr_uuid)}"')
            params.append(attr_uuid)

        # B608 false positive: case_statements contain only column names; values are bound via ? placeholders
        sql = f"""
            select a.*,
                {', '.join(case_statements) if case_statements else '""'}
            from account as a
            left join account_attr as aa on aa."account" = a.uuid
            left join attribute_type as at on aa.attr_uuid = at.id
            group by a.uuid
        """  # nosec

        accounts = (pd.read_sql_query(sql, self._db.connection, index_col='uuid', params=params)
                          .rename_axis('accountId'))

        # Convert attribute values based on converterClass
        accounts = convert_attribute_types(accounts, account_attrs)

        return AccountSchema.validate(accounts)

    def _parse_taxonomies(self) -> tuple[dict[str, Taxonomy], pd.DataFrame]:
        cursor = self._db.connection.cursor()
        cursor.execute('SELECT uuid, name FROM taxonomy')
        taxonomies = {str(row[0]): Taxonomy(uuid=str(row[0]), name=str(row[1])) for row in cursor.fetchall()}

        if not taxonomies:
            return {}, pd.DataFrame()

        assignments = pd.read_sql_query("""
            SELECT t.name AS taxonomyName, ta.item AS itemId, ta.item_type AS itemType,
                   tc.name AS categoryName, ta.weight
            FROM taxonomy_assignment ta
            JOIN taxonomy t ON t.uuid = ta.taxonomy
            JOIN taxonomy_category tc ON tc.uuid = ta.category
        """, self._db.connection)

        return taxonomies, assignments

    def _get_property(self, name: str) -> str | None:
        cursor = self._db.connection.cursor()
        cursor.execute('select value from property where name = ?', (name, ))

        result = cursor.fetchone()
        if result is None:
            return None

        return str(result[0])

    def _get_attributes(self, entity: str) -> dict[str, Attribute]:
        cursor = self._db.connection.cursor()
        cursor.execute("SELECT id, name, converterClass FROM attribute_type WHERE target = ? AND id NOT IN ('logo')", (entity, ))

        return {str(row[0]): Attribute(uuid=str(row[0]), name=str(row[1]), converter=str(row[2])) for row in cursor.fetchall()}


class CachedPpPortfolioBuilder:  # pylint: disable=too-few-public-methods
    _config: Dict[str, Any]

    def __init__(self, config: Dict[str, Any] | None = None):
        self._config = config if config is not None else {}

    def construct(self, file: Path) -> Portfolio:
        use_cache_file = False

        try:
            cache_path = get_cache_path(file)

            if cache_path.exists():
                log.debug('Using cache database "%s"', cache_path)
                use_cache_file = True
            else:
                log.debug('Cache file not found, will create at "%s"', cache_path)

            db = Ppxml2dbWrapper(dbname=str(cache_path))
        except Exception as e:  # pylint: disable=broad-exception-caught
            log.warning(
                'Failed to initialize cache database (%s), using in-memory database',
                str(e)
            )
            db = Ppxml2dbWrapper(dbname=DB_NAME_IN_MEMORY)
            cache_path = None
            use_cache_file = False

        builder = PpPortfolioBuilder(db, self._config)

        # Override the open behavior for cache hits
        if use_cache_file:
            # Cache hit: skip XML parsing by replacing open with a no-op
            original_open = db.open
            db.open = lambda _: None  # type: ignore
            portfolio = builder.construct(file)
            db.open = original_open  # type: ignore
        else:
            # Cache miss: normal construction which will call db.open()
            portfolio = builder.construct(file)

            if cache_path is not None:
                try:
                    cleanup_old_cache_files(file)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    log.warning('Failed to cleanup old cache files: %s', str(e))

        return portfolio

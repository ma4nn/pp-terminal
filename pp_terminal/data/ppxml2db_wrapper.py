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

import sqlite3
import os
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import IO

from ppxml2db.ppxml2db import PortfolioPerformanceXML2DB
from ppxml2db import dbhelper
from ppxml2db import ppxml2db_init

from pp_terminal.exceptions import InputError
from pp_terminal.data.xml_id_reference_converter import convert_id_references, has_id_references

log = logging.getLogger(__name__)
logging.getLogger('ppxml2db.dbhelper').setLevel(logging.INFO)  # reducing some "noise"


DB_NAME_IN_MEMORY = ':memory:'


class Ppxml2dbWrapper:
    _connection: sqlite3.Connection
    _schema_path: str
    _cursor: sqlite3.Cursor

    def __init__(self, dbname: str = DB_NAME_IN_MEMORY) -> None:
        self._schema_path = os.path.dirname(dbhelper.__file__) + '/'

        try:
            dbhelper.init('sqlite', dbname)  # type: ignore
            if dbhelper.db is None:
                raise RuntimeError('could not establish database connection')

            self._connection = dbhelper.db  # @todo db connection is global state!
            self._cursor = self._connection.cursor()
            self._install()
        except Exception as e:
            raise RuntimeError('error during database initialization for ' + dbname) from e

    def open(self, file: Path) -> None:
        try:
            with file.open(mode='rb') as source:
                xml: IO[bytes] = source if has_id_references(source) else convert_id_references(source)
                conv = PortfolioPerformanceXML2DB(xml)  # type: ignore
                conv.iterparse()  # type: ignore

            self._connection.commit()

            self._validate()
        except (FileNotFoundError, InputError) as e:
            raise e
        except Exception as e:
            raise InputError('unable to import the Portfolio Performance xml file "' + file.name + '"') from e

    def close(self) -> None:
        self._connection.close()  # database is deleted

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def _install(self) -> None:
        # Check if tables already exist (for cached databases)
        try:
            self._validate()
            log.debug('Database tables already exist, skipping initialization scripts')
        except Exception:  # pylint: disable=broad-exception-caught
            # Tables don't exist, create them using official ppxml2db schema
            self._create_tables()

    def _create_tables(self) -> None:
        # Use ppxml2db_init.main() to ensure schema compatibility
        args = SimpleNamespace(dbtype='sqlite', recreate=False)
        current_dir = os.getcwd()
        try:
            os.chdir(self._schema_path)
            ppxml2db_init.main(args)  # type: ignore
            log.debug('ppxml2db schema initialized')
        finally:
            os.chdir(current_dir)

    def _validate(self) -> None:
        self._cursor.execute("select value as client_version from property where name = 'version'")
        row = self._cursor.fetchone()
        if row is None:
            raise RuntimeError('missing client version in xml file')

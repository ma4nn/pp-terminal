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
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest
from _pytest.fixtures import TopRequest

from pp_terminal.exceptions import InputError
from pp_terminal.data.pp_portfolio_builder import PpPortfolioBuilder, CachedPpPortfolioBuilder
from pp_terminal.data.ppxml2db_wrapper import Ppxml2dbWrapper
from pp_terminal.domain.schemas import TransactionType

EXPECTED_AMOUNT_SIGNS = {
    TransactionType.BUY: -1,
    TransactionType.SELL: 1,
    TransactionType.DELIVERY_INBOUND: 1,
    TransactionType.DELIVERY_OUTBOUND: 1,
    TransactionType.TRANSFER_IN: 1,
    TransactionType.TRANSFER_OUT: -1,
    TransactionType.DEPOSIT: 1,
    TransactionType.REMOVAL: -1,
    TransactionType.INTEREST: 1,
    TransactionType.INTEREST_CHARGE: -1,
    TransactionType.FEES_REFUND: 1,
    TransactionType.FEES: -1,
    TransactionType.DIVIDENDS: 1,
    TransactionType.TAXES: -1,
    TransactionType.TAX_REFUND: 1,
}


def test_import_non_existent_file() -> None:
    with pytest.raises(FileNotFoundError):
        CachedPpPortfolioBuilder().construct(Path('non-existing.xml'))


@pytest.mark.parametrize("xml_file", ['invalid.xml', 'other.xml'])
def test_import_invalid_xml(request: TopRequest, xml_file: str) -> None:
    with pytest.raises(InputError):
        PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / xml_file)


def test_import_xml_without_ids(request: TopRequest) -> None:
    """Portfolio Performance's default xml flavor uses relative path references instead of id attributes."""
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.xml')

    assert not portfolio.securities.empty


def test_import_xml_without_ids_matches_id_flavor(request: TopRequest) -> None:
    fixtures = request.path.parent.parent / 'fixtures'
    without_ids = PpPortfolioBuilder().construct(fixtures / 'kommer.xml')
    with_ids = PpPortfolioBuilder().construct(fixtures / 'kommer.ids.xml')

    assert without_ids.base_currency == with_ids.base_currency
    assert without_ids.taxonomies == with_ids.taxonomies
    assert without_ids.all_attributes == with_ids.all_attributes

    # the two fixtures are exports of the same portfolio, but the "with ids" one carries one additional
    # security attribute value; xml bookkeeping columns differ accordingly and are irrelevant downstream
    diverging = ['2baac2d0-459b-4b41-a0ef-d7dad0866892', '_xmlid', '_order']
    for name in ('securities', 'prices', 'taxonomy_assignments', 'securities_accounts', 'deposit_accounts',
                 'securities_account_transactions', 'deposit_account_transactions'):
        pd.testing.assert_frame_equal(getattr(without_ids, name).drop(columns=diverging, errors='ignore'),
                                      getattr(with_ids, name).drop(columns=diverging, errors='ignore'))


def test_import_pp_empty_xml(request: TopRequest) -> None:
    CachedPpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'empty.ids.xml')


def test_import_xml_with_null_property_value(request: TopRequest) -> None:
    """PP v69+ can have properties with empty/null values (e.g. portfolio-chart-details)."""
    CachedPpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'empty_null_prop.ids.xml')


def test_import_missing_base_currency_property(request: TopRequest) -> None:
    """A database without baseCurrency can occur when a cache database is reused and XML parsing is skipped."""
    xml_file = request.path.parent.parent / 'fixtures' / 'empty.ids.xml'
    db = Ppxml2dbWrapper()
    db.open(xml_file)
    db.connection.execute("delete from property where name = 'baseCurrency'")

    with patch.object(db, 'open'), pytest.raises(InputError):
        PpPortfolioBuilder(db).construct(xml_file)


def test_transaction_amount_sign_and_scaling_per_type(request: TopRequest) -> None:
    """Fixture stores each type once with amount 123456 (cents); portfolio transactions with shares 250000000 (10^8 scale)."""
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'transaction_types.ids.xml')

    transactions = pd.concat([portfolio.deposit_account_transactions, portfolio.securities_account_transactions])

    for transaction_type in TransactionType:
        amounts = transactions.loc[transactions['type'] == transaction_type.value, 'amount']
        assert amounts.tolist() == [EXPECTED_AMOUNT_SIGNS[transaction_type] * 1234.56], transaction_type.value

    assert portfolio.securities_account_transactions['shares'].tolist() == [2.5] * 6


def test_xml_file_opened_readonly(request: TopRequest) -> None:
    """Verify that Portfolio Performance XML files are opened in read-only mode."""
    xml_file_path = request.path.parent.parent / 'fixtures' / 'empty.ids.xml'

    # Track the mode parameter passed to file.open()
    original_open = Path.open
    open_call_args: dict[str, Any] = {}

    def tracked_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        open_call_args['mode'] = kwargs.get('mode', 'r')
        open_call_args['path'] = self
        return original_open(self, *args, **kwargs)

    with patch.object(Path, 'open', tracked_open):
        PpPortfolioBuilder().construct(xml_file_path)

    assert 'mode' in open_call_args, "Path.open() was not called"
    assert open_call_args['mode'] == 'rb', \
        f"Expected file to be opened with mode='rb', but got mode='{open_call_args['mode']}'"
    assert open_call_args['path'] == xml_file_path, \
        f"Expected {xml_file_path} to be opened, but got {open_call_args['path']}'"


def test_cache_disabled_uses_in_memory(request: TopRequest, tmp_path: Path) -> None:
    """Test that use_cache=False uses in-memory database."""
    xml_file = request.path.parent.parent / 'fixtures' / 'empty.ids.xml'

    # Create temporary copy to avoid interference with other tests
    temp_xml = tmp_path / 'test.xml'
    temp_xml.write_bytes(xml_file.read_bytes())

    # Build portfolio without caching
    PpPortfolioBuilder().construct(temp_xml)

    # Verify no cache file was created
    cache_files = list(tmp_path.glob('.test.*.pp-terminal.db'))
    assert len(cache_files) == 0

def test_cache_hit_reuses_existing(request: TopRequest, tmp_path: Path) -> None:
    """Test that existing valid cache is reused."""
    xml_file = request.path.parent.parent / 'fixtures' / 'empty.ids.xml'

    # Create temporary copy
    temp_xml = tmp_path / 'test.xml'
    temp_xml.write_bytes(xml_file.read_bytes())

    # First build: creates cache
    CachedPpPortfolioBuilder().construct(temp_xml)

    # Get cache file
    cache_files = list(tmp_path.glob('.test.*.pp-terminal.db'))
    assert len(cache_files) == 1
    cache_file = cache_files[0]
    cache_mtime = cache_file.stat().st_mtime

    # Second build: should reuse cache (we can't easily verify open() wasn't called
    # without complex mocking, but we can verify the cache file wasn't recreated)
    CachedPpPortfolioBuilder().construct(temp_xml)

    # Cache file should still exist and not be recreated
    assert cache_file.exists()
    assert cache_file.stat().st_mtime == cache_mtime

def test_cache_invalidation_on_xml_change(request: TopRequest, tmp_path: Path) -> None:
    """Test that cache is invalidated when XML changes."""
    xml_file = request.path.parent.parent / 'fixtures' / 'empty.ids.xml'

    # Create temporary copy
    temp_xml = tmp_path / 'test.xml'
    temp_xml.write_bytes(xml_file.read_bytes())

    # First build: creates cache
    CachedPpPortfolioBuilder().construct(temp_xml)

    # Get original cache file
    old_cache_files = list(tmp_path.glob('.test.*.pp-terminal.db'))
    assert len(old_cache_files) == 1
    old_cache_file = old_cache_files[0]

    # Modify XML file
    content = temp_xml.read_text()
    temp_xml.write_text(content + "<!-- modified -->")

    # Second build: should create new cache with different checksum
    CachedPpPortfolioBuilder().construct(temp_xml)

    # New cache file should exist
    new_cache_files = list(tmp_path.glob('.test.*.pp-terminal.db'))
    assert len(new_cache_files) == 1
    new_cache_file = new_cache_files[0]

    # Old cache should be cleaned up, new cache should have different name
    assert not old_cache_file.exists()
    assert new_cache_file.name != old_cache_file.name

def test_old_cache_cleanup(request: TopRequest, tmp_path: Path) -> None:
    """Test that old cache files are deleted."""
    xml_file = request.path.parent.parent / 'fixtures' / 'empty.ids.xml'

    # Create temporary copy
    temp_xml = tmp_path / 'test.xml'
    temp_xml.write_bytes(xml_file.read_bytes())

    # Create fake old cache files
    (tmp_path / '.test.abc123.pp-terminal.db').write_text('old cache 1')
    (tmp_path / '.test.def456.pp-terminal.db').write_text('old cache 2')

    # Build portfolio: should cleanup old caches
    CachedPpPortfolioBuilder().construct(temp_xml)

    # Verify old caches deleted, only current cache exists
    cache_files = list(tmp_path.glob('.test.*.pp-terminal.db'))
    assert len(cache_files) == 1
    assert not (tmp_path / '.test.abc123.pp-terminal.db').exists()
    assert not (tmp_path / '.test.def456.pp-terminal.db').exists()

def test_cache_fallback_on_io_error(request: TopRequest, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test graceful fallback to in-memory on cache I/O error."""
    xml_file = request.path.parent.parent / 'fixtures' / 'empty.ids.xml'

    # Create temporary copy in read-only directory to simulate I/O error
    readonly_dir = tmp_path / 'readonly'
    readonly_dir.mkdir()
    temp_xml = readonly_dir / 'test.xml'
    temp_xml.write_bytes(xml_file.read_bytes())

    # Make directory read-only
    readonly_dir.chmod(0o555)

    try:
        # Should fall back to in-memory mode
        CachedPpPortfolioBuilder().construct(temp_xml)

        # Verify warning was logged
        assert any('Cache unavailable' in record.message or 'Failed to initialize cache' in record.message
                  for record in caplog.records)
    finally:
        # Restore permissions for cleanup
        readonly_dir.chmod(0o755)

def test_securities_percent_attributes_converted(request: TopRequest) -> None:
    """Test that securities with PercentPlainConverter attributes are loaded as decimals, not raw values."""
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

    exempt_attr_uuid = '2baac2d0-459b-4b41-a0ef-d7dad0866892'
    assert exempt_attr_uuid in portfolio.securities.columns

    securities_with_exemption = portfolio.securities[portfolio.securities[exempt_attr_uuid].notna()]
    assert len(securities_with_exemption) > 0, "Should have at least one security with exemption rate"

    # Verify values are decimals (0.0-1.0 range), not raw percentages (0-100)
    for value in securities_with_exemption[exempt_attr_uuid]:
        assert isinstance(value, float), f"Exemption rate should be float, got {type(value)}"
        assert 0.0 <= value <= 1.0, f"Exemption rate should be normalized (0.0-1.0), got {value}"

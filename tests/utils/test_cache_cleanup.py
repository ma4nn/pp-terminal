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

import pytest

from pp_terminal.utils.cache import cleanup_old_cache_files, get_cache_path

from tests.utils.test_cache_path import SPECIAL_CHARACTER_STEMS

OUTDATED_CHECKSUM = 'a' * 64


def test_cleanup_removes_old_cache_files(tmp_path: Path) -> None:
    """Test that old cache files are removed."""
    xml_file = tmp_path / "test.xml"
    xml_file.write_text("<?xml version='1.0'?><test>data</test>")

    # Get current cache path
    current_cache = get_cache_path(xml_file)

    # Create old cache files with different checksums
    old_cache1 = tmp_path / f".test.{OUTDATED_CHECKSUM}.pp-terminal.db"
    old_cache2 = tmp_path / f".test.{'b' * 64}.pp-terminal.db"
    old_cache1.write_text("old cache")
    old_cache2.write_text("old cache")

    # Create current cache file
    current_cache.write_text("current cache")

    # Cleanup
    cleanup_old_cache_files(xml_file)

    # Verify old files deleted, current file preserved
    assert not old_cache1.exists()
    assert not old_cache2.exists()
    assert current_cache.exists()

def test_cleanup_preserves_current_cache(tmp_path: Path) -> None:
    """Test that current cache file is not deleted."""
    xml_file = tmp_path / "test.xml"
    xml_file.write_text("<?xml version='1.0'?><test>data</test>")

    # Get and create current cache
    current_cache = get_cache_path(xml_file)
    current_cache.write_text("current cache")

    cleanup_old_cache_files(xml_file)

    assert current_cache.exists()
    assert current_cache.read_text() == "current cache"

def test_cleanup_no_cache_files(tmp_path: Path) -> None:
    """Test cleanup when no cache files exist."""
    xml_file = tmp_path / "test.xml"
    xml_file.write_text("<?xml version='1.0'?><test>data</test>")

    # Should not raise exception
    cleanup_old_cache_files(xml_file)

def test_cleanup_ignores_unrelated_files(tmp_path: Path) -> None:
    """Test that cleanup only targets cache files for this XML."""
    xml_file = tmp_path / "test.xml"
    xml_file.write_text("<?xml version='1.0'?><test>data</test>")

    # Create unrelated files
    other_file1 = tmp_path / "test.xml.backup"
    other_file2 = tmp_path / f".other.{OUTDATED_CHECKSUM}.pp-terminal.db"
    other_file1.write_text("backup")
    other_file2.write_text("other cache")

    cleanup_old_cache_files(xml_file)

    # Unrelated files should be preserved
    assert other_file1.exists()
    assert other_file2.exists()

def test_cleanup_handles_already_deleted_file(tmp_path: Path) -> None:
    """Test that cleanup handles race condition when file is already deleted."""
    xml_file = tmp_path / "test.xml"
    xml_file.write_text("<?xml version='1.0'?><test>data</test>")

    # Create an old cache file that we'll delete manually to simulate race condition
    old_cache = tmp_path / f".test.{OUTDATED_CHECKSUM}.pp-terminal.db"
    old_cache.write_text("old cache")

    # Delete the file before cleanup runs (simulating concurrent deletion)
    old_cache.unlink()

    # Should not raise exception
    cleanup_old_cache_files(xml_file)

@pytest.mark.parametrize("stem", SPECIAL_CHARACTER_STEMS)
def test_cleanup_removes_old_cache_files_with_special_characters(tmp_path: Path, stem: str) -> None:
    """Characters like "[" must not turn the filename into a glob pattern that misses its own cache files."""
    xml_file = tmp_path / f"{stem}.xml"
    xml_file.write_text("<?xml version='1.0'?><test>data</test>")

    current_cache = get_cache_path(xml_file)
    current_cache.write_text("current cache")
    old_cache = tmp_path / f".{stem}.{OUTDATED_CHECKSUM}.pp-terminal.db"
    old_cache.write_text("old cache")

    cleanup_old_cache_files(xml_file)

    assert not old_cache.exists()
    assert current_cache.exists()

def test_cleanup_keeps_cache_of_xml_file_matching_as_glob_pattern(tmp_path: Path) -> None:
    """"a[b].xml" must not delete the cache files of "ab.xml"."""
    xml_file = tmp_path / "a[b].xml"
    xml_file.write_text("<?xml version='1.0'?><test>data</test>")

    foreign_cache = tmp_path / f".ab.{OUTDATED_CHECKSUM}.pp-terminal.db"
    foreign_cache.write_text("cache of ab.xml")

    cleanup_old_cache_files(xml_file)

    assert foreign_cache.exists()

def test_cleanup_keeps_cache_of_xml_file_sharing_the_stem_prefix(tmp_path: Path) -> None:
    """"test.xml" must not delete the cache files of "test.extra.xml"."""
    xml_file = tmp_path / "test.xml"
    xml_file.write_text("<?xml version='1.0'?><test>data</test>")

    foreign_cache = tmp_path / f".test.extra.{OUTDATED_CHECKSUM}.pp-terminal.db"
    foreign_cache.write_text("cache of test.extra.xml")

    cleanup_old_cache_files(xml_file)

    assert foreign_cache.exists()

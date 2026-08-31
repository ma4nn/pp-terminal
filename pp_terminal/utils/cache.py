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

import hashlib
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_FILE_SUFFIX = ".pp-terminal.db"
_CHECKSUM_PATTERN = "[0-9a-f]+"


def checksum(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def _get_cache_filename_prefix(xml_file: Path) -> str:
    return xml_file.stem if xml_file.name.startswith(".") else f".{xml_file.stem}"


def _get_cache_filename_pattern(xml_file: Path) -> re.Pattern[str]:
    # the filename is data, never a pattern, so "$", "[" or "*" in it must stay literal
    return re.compile(re.escape(f"{_get_cache_filename_prefix(xml_file)}.") + _CHECKSUM_PATTERN + re.escape(_FILE_SUFFIX))


def get_cache_path(xml_file: Path) -> Path:
    return xml_file.parent / f"{_get_cache_filename_prefix(xml_file)}.{checksum(xml_file)}{_FILE_SUFFIX}"


def cleanup_old_cache_files(xml_file: Path) -> None:
    """
    Delete the cache files of the given XML file that belong to an outdated checksum.

    Raises:
        OSError: If cache files cannot be deleted (logged as warning, not raised)
    """

    cache_pattern = _get_cache_filename_pattern(xml_file)
    current_cache_name = get_cache_path(xml_file).name

    try:
        for cache_file in xml_file.parent.iterdir():
            if cache_file.name == current_cache_name or not cache_pattern.fullmatch(cache_file.name):
                continue

            try:
                cache_file.unlink()
                log.debug('Deleted old cache file "%s"', cache_file)
            except FileNotFoundError:
                # File already deleted (race condition), ignore
                pass
            except OSError as e:
                log.warning('Failed to delete old cache file "%s": %s', cache_file, str(e))
    except Exception as e:  # pylint: disable=broad-exception-caught
        log.warning('Failed to cleanup old cache files: %s', str(e))

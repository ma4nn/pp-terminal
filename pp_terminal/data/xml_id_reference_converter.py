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

import io
import re
from typing import IO

import lxml.etree as ET  # pylint: disable=c-extension-no-member

from pp_terminal.exceptions import InputError

STEP_PATTERN = re.compile(r'^(.+?)(?:\[(\d+)\])?$')


def has_id_references(source: IO[bytes]) -> bool:
    """Detects whether the xml file uses XStream ID_REFERENCES mode (root element carries an id attribute)."""
    try:
        for _, element in ET.iterparse(source, events=('start',)):  # pylint: disable=c-extension-no-member
            return element.get('id') is not None
        return False
    finally:
        source.seek(0)


def convert_id_references(source: IO[bytes]) -> io.BytesIO:
    """Rewrites XStream relative path references into ID_REFERENCES style, which is what ppxml2db understands."""
    parser = ET.XMLParser(resolve_entities=False, no_network=True)  # pylint: disable=c-extension-no-member
    tree = ET.parse(source, parser)  # pylint: disable=c-extension-no-member
    root = tree.getroot()

    _assign_ids(root)
    _rewrite_references(root)

    return io.BytesIO(ET.tostring(tree, xml_declaration=True, encoding='UTF-8'))  # pylint: disable=c-extension-no-member


def _assign_ids(root: ET.Element) -> None:  # pylint: disable=c-extension-no-member
    counter = 0
    for element in root.iter():
        if not isinstance(element.tag, str) or element.get('reference') is not None:
            continue
        counter += 1
        element.set('id', str(counter))


def _rewrite_references(root: ET.Element) -> None:  # pylint: disable=c-extension-no-member
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        path = element.get('reference')
        if path is None:
            continue

        target = _resolve(element, root, path)
        target_id = target.get('id')
        if target_id is None:
            raise InputError(f'reference "{path}" in line {element.sourceline} points to another reference')

        element.set('reference', target_id)


def _resolve(element: ET.Element, root: ET.Element, path: str) -> ET.Element:  # pylint: disable=c-extension-no-member
    steps = path.split('/')
    if path.startswith('/'):
        # absolute path: the leading empty segment is followed by the name of the root element
        if len(steps) < 2 or steps[1] != root.tag:
            raise InputError(f'unable to resolve reference "{path}" in line {element.sourceline}: unknown root element')
        current, steps = root, steps[2:]
    else:
        current = element

    for step in steps:
        current = _walk(current, step, path, element.sourceline)

    return current


def _walk(current: ET.Element, step: str, path: str, line: int | None) -> ET.Element:  # pylint: disable=c-extension-no-member
    if step == '..':
        parent = current.getparent()
        if parent is None:
            raise InputError(f'unable to resolve reference "{path}" in line {line}: no parent element')
        return parent

    match = STEP_PATTERN.match(step)
    if match is None:
        raise InputError(f'unable to resolve reference "{path}" in line {line}: unsupported path step "{step}"')

    tag, index = match.group(1), int(match.group(2) or 1)
    children = [child for child in current if child.tag == tag]
    if not 1 <= index <= len(children):
        raise InputError(f'unable to resolve reference "{path}" in line {line}: no element "{step}"')

    return children[index - 1]

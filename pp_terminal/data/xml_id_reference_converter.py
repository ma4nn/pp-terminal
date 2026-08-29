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

# tags whose id attribute ppxml2db reads to tell a definition from a back-reference; any other element
# only needs an id once something references it, and giving all of them one costs real memory
ID_TAGS = frozenset({
    'security', 'account', 'referenceAccount', 'accountFrom', 'accountTo',
    'portfolio', 'portfolioFrom', 'portfolioTo',
    'account-transaction', 'accountTransaction', 'portfolio-transaction', 'portfolioTransaction',
    'transactionFrom', 'transactionTo', 'crossEntry', 'root', 'classification',
})


def has_id_references(source: IO[bytes]) -> bool:
    """True for XStream ID_REFERENCES mode, where the root element carries an id."""
    try:
        for _, element in ET.iterparse(source, events=('start',)):  # pylint: disable=c-extension-no-member
            return element.get('id') is not None
        return False
    finally:
        source.seek(0)


def convert_id_references(source: IO[bytes]) -> io.BytesIO:
    """Rewrites XStream path references into the ID_REFERENCES style ppxml2db understands."""
    parser = ET.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True)  # pylint: disable=c-extension-no-member
    tree = ET.parse(source, parser)  # pylint: disable=c-extension-no-member
    root = tree.getroot()

    references = _resolve_references(root)
    # membership in the target set is lxml proxy identity, which only holds while references keeps them alive
    _assign_ids(root, {target for _, target in references})

    for element, target in references:
        target_id = target.get('id')
        if target_id is None:
            raise InputError(f'reference "{element.get("reference")}" in line {element.sourceline} points to another reference')
        element.set('reference', target_id)

    return io.BytesIO(ET.tostring(tree, xml_declaration=True, encoding='UTF-8'))  # pylint: disable=c-extension-no-member


def _resolve_references(root: ET.Element) -> list[tuple[ET.Element, ET.Element]]:  # pylint: disable=c-extension-no-member
    references = []
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        path = element.get('reference')
        if path is not None:
            references.append((element, _resolve(element, root, path)))
    return references


def _assign_ids(root: ET.Element, targets: set[ET.Element]) -> None:  # pylint: disable=c-extension-no-member
    counter = 0
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        # drop foreign ids before anything can skip past: ppxml2db tells a definition from a back-reference
        # by this attribute, and an id we did not assign could collide with the synthetic sequence
        element.attrib.pop('id', None)
        if element.get('reference') is not None:
            continue
        # the root always gets one, otherwise has_id_references would not recognize the converted result
        if element is not root and element.tag not in ID_TAGS and element not in targets:
            continue
        counter += 1
        element.set('id', str(counter))


def _resolve(element: ET.Element, root: ET.Element, path: str) -> ET.Element:  # pylint: disable=c-extension-no-member
    steps = path.split('/')
    if path.startswith('/'):
        # the leading empty segment is followed by the root element name
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

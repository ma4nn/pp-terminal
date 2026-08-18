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

# pylint: disable=c-extension-no-member
import io

import lxml.etree as ET
import pytest
from _pytest.fixtures import TopRequest

from pp_terminal.exceptions import InputError
from pp_terminal.data.xml_id_reference_converter import convert_id_references, has_id_references


def convert(xml: str) -> ET.Element:
    return ET.parse(convert_id_references(io.BytesIO(xml.encode()))).getroot()


def reference_targets(root: ET.Element) -> list[tuple[str, str]]:
    """Maps each reference to its target's structural path, making both xml flavors comparable."""
    paths: dict[ET.Element, str] = {}

    def walk(element: ET.Element, path: str) -> None:
        paths[element] = path
        occurrences: dict[str, int] = {}
        for child in element:
            if not isinstance(child.tag, str):
                continue
            occurrences[child.tag] = occurrences.get(child.tag, 0) + 1
            walk(child, f'{path}/{child.tag}[{occurrences[child.tag]}]')

    walk(root, root.tag)
    definitions = {element.get('id'): element for element in root.iter() if isinstance(element.tag, str) and element.get('id')}

    return [(paths[element], paths[definitions[str(element.get('reference'))]])
            for element in root.iter() if isinstance(element.tag, str) and element.get('reference')]


def test_ids_are_assigned_in_document_order() -> None:
    root = convert('<client><securities><security><uuid>x</uuid></security></securities></client>')

    assert [(el.tag, el.get('id')) for el in root.iter()] == [
        ('client', '1'), ('securities', '2'), ('security', '3'), ('uuid', '4')
    ]


def test_reference_elements_do_not_get_an_id() -> None:
    root = convert('<client><portfolio><uuid>x</uuid></portfolio><account reference="../portfolio"/></client>')

    account = root.find('account')
    assert account is not None
    assert account.get('id') is None
    assert account.get('reference') == root.find('portfolio').get('id')


def test_pre_existing_ids_are_replaced() -> None:
    """Keeping them would let the synthetic sequence collide with an id already in use."""
    root = convert('<client><portfolio id="3"><uuid>x</uuid></portfolio><other/><account id="4" reference="../portfolio"/></client>')

    ids = [element.get('id') for element in root.iter() if element.get('id') is not None]
    assert len(ids) == len(set(ids)), f'ids must stay unique, got {ids}'
    assert root.find('account').get('id') is None, 'a reference must never keep an id, that is how ppxml2db spots definitions'
    assert root.find('account').get('reference') == root.find('portfolio').get('id')


def test_reference_with_index_predicate() -> None:
    root = convert("""<client>
        <securities><security><uuid>a</uuid></security><security><uuid>b</uuid></security></securities>
        <link reference="../securities/security[2]"/>
    </client>""")

    assert root.find('link').get('reference') == root.findall('securities/security')[1].get('id')


def test_reference_without_predicate_resolves_to_first_matching_child() -> None:
    """XStream omits an explicit [1]; a bare step is not "any same-named child"."""
    root = convert("""<client>
        <securities><security><uuid>a</uuid></security><security><uuid>b</uuid></security></securities>
        <link reference="../securities/security"/>
    </client>""")

    assert root.find('link').get('reference') == root.findall('securities/security')[0].get('id')


def test_pure_ancestor_reference() -> None:
    root = convert('<client><portfolio><transactions><entry><parent reference="../../.."/></entry></transactions></portfolio></client>')

    assert root.find('.//parent').get('reference') == root.find('portfolio').get('id')


def test_absolute_reference() -> None:
    root = convert("""<client>
        <securities><security><uuid>a</uuid></security></securities>
        <link reference="/client/securities/security"/>
    </client>""")

    assert root.find('link').get('reference') == root.find('securities/security').get('id')


@pytest.mark.parametrize("reference", [
    '../securities/security[3]',  # index out of range
    '../securities/security[0]',  # XStream indexes are 1-based
    '../nowhere',                 # no such child
    '../../../..',                # beyond the root element
    '/nonclient/securities/security',  # unknown root element
])
def test_unresolvable_reference(reference: str) -> None:
    with pytest.raises(InputError):
        convert(f'<client><securities><security><uuid>a</uuid></security></securities><link reference="{reference}"/></client>')


def test_reference_pointing_to_another_reference() -> None:
    with pytest.raises(InputError):
        convert("""<client>
            <securities><security><uuid>a</uuid></security></securities>
            <first reference="../securities/security"/>
            <second reference="../first"/>
        </client>""")


@pytest.mark.parametrize("xml, expected", [
    ('<client id="1"><version>66</version></client>', True),
    ('<client><version>66</version></client>', False),
    ('<client version="66"></client>', False),
])
def test_has_id_references(xml: str, expected: bool) -> None:
    source = io.BytesIO(xml.encode())

    assert has_id_references(source) is expected
    assert source.tell() == 0, 'the stream must be rewound for the subsequent parse'


def test_references_resolve_like_the_id_flavor(request: TopRequest) -> None:
    """Every reference must end up at the same element as in the "XML with ids" twin."""
    fixtures = request.path.parent.parent / 'fixtures'
    with (fixtures / 'kommer.xml').open(mode='rb') as source:
        converted = ET.parse(convert_id_references(source)).getroot()

    expected = reference_targets(ET.parse(str(fixtures / 'kommer.ids.xml')).getroot())

    assert len(expected) > 0
    assert reference_targets(converted) == expected

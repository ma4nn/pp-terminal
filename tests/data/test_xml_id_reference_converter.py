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
from pp_terminal.data.xml_id_reference_converter import ID_TAGS, convert_id_references, has_id_references


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
    """Only the root and the tags ppxml2db inspects get one - iding all 36k elements of a real file costs memory."""
    root = convert('<client><securities><security><uuid>x</uuid></security></securities></client>')

    assert [(el.tag, el.get('id')) for el in root.iter()] == [
        ('client', '1'), ('securities', None), ('security', '2'), ('uuid', None)
    ]


# spelled out rather than derived from ID_TAGS, so trimming that set fails here instead of silently
# dropping entities; these are every tag whose id attribute ppxml2db reads (ppxml2db.py:412-421 and 527)
PPXML2DB_ID_TAGS = (
    'security', 'account', 'referenceAccount', 'accountFrom', 'accountTo',
    'portfolio', 'portfolioFrom', 'portfolioTo',
    'account-transaction', 'accountTransaction', 'portfolio-transaction', 'portfolioTransaction',
    'transactionFrom', 'transactionTo', 'crossEntry', 'root', 'classification',
)


def test_tags_ppxml2db_inspects_always_get_an_id() -> None:
    """Nothing references these, yet ppxml2db needs the id to tell a definition from a back-reference."""
    assert ID_TAGS == frozenset(PPXML2DB_ID_TAGS)

    root = convert('<client><wrapper>' + ''.join(f'<{tag}/>' for tag in PPXML2DB_ID_TAGS) + '</wrapper></client>')

    missing = [tag for tag in PPXML2DB_ID_TAGS if root.find(f'wrapper/{tag}').get('id') is None]
    assert not missing, f'ppxml2db reads the id attribute of {missing}'


def test_referenced_element_gets_an_id_regardless_of_its_tag() -> None:
    root = convert('<client><some><thing/><thing/></some><link reference="../some/thing[2]"/></client>')

    referenced = root.findall('some/thing')[1]
    assert referenced.get('id') is not None
    assert root.findall('some/thing')[0].get('id') is None
    assert root.find('link').get('reference') == referenced.get('id')


def test_reference_elements_do_not_get_an_id() -> None:
    root = convert('<client><portfolio><uuid>x</uuid></portfolio><account reference="../portfolio"/></client>')

    account = root.find('account')
    assert account is not None
    assert account.get('id') is None
    assert account.get('reference') == root.find('portfolio').get('id')


def test_pre_existing_ids_are_replaced() -> None:
    """Keeping them would let the synthetic sequence collide with an id already in use."""
    # 'stray' is neither in ID_TAGS nor a reference target, so nothing would otherwise overwrite its id
    root = convert('<client><portfolio id="3"><uuid>x</uuid></portfolio><stray id="2"/><other/>'
                   '<account id="4" reference="../portfolio"/></client>')

    ids = [element.get('id') for element in root.iter() if element.get('id') is not None]
    assert len(ids) == len(set(ids)), f'ids must stay unique, got {ids}'
    assert root.find('stray').get('id') is None, 'an id we do not assign ourselves can collide with the synthetic sequence'
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

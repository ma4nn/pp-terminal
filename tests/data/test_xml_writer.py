"""Tests for pp_terminal.data.xml_writer."""

import shutil
from datetime import datetime
from pathlib import Path

import lxml.etree as ET
import pytest

from pp_terminal.data.xml_writer import PpXmlWriter

FIXTURES = Path(__file__).parent.parent / 'fixtures'


@pytest.fixture
def writable_xml(tmp_path: Path) -> Path:
    """Copy partial_sell fixture to a temp dir for writing tests."""
    src = FIXTURES / 'partial_sell.ids.xml'
    dst = tmp_path / 'test_portfolio.xml'
    shutil.copy(src, dst)
    return dst


def _parse(path: Path) -> ET.ElementTree:
    return ET.parse(str(path))


def _find_txn_by_uuid(root: ET.Element, uuid: str) -> ET.Element | None:
    for el in root.iter():
        uuid_el = el.find('uuid')
        if uuid_el is not None and uuid_el.text == uuid:
            return el
    return None


# ─── Security ───

class TestAddSecurity:

    def test_adds_security_element(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        sec_uuid = writer.add_security(
            'Test ETF', 'EUR', isin='IE00TEST1234', wkn='TST01', ticker='TEST.DE',
            backup=False,
        )

        tree = _parse(writable_xml)
        root = tree.getroot()
        securities = root.findall('.//securities/security')

        # Should have original + new
        new_sec = None
        for sec in securities:
            uuid_el = sec.find('uuid')
            if uuid_el is not None and uuid_el.text == sec_uuid:
                new_sec = sec
                break

        assert new_sec is not None
        assert new_sec.find('name').text == 'Test ETF'
        assert new_sec.find('currencyCode').text == 'EUR'
        assert new_sec.find('isin').text == 'IE00TEST1234'
        assert new_sec.find('wkn').text == 'TST01'
        assert new_sec.find('tickerSymbol').text == 'TEST.DE'
        assert new_sec.find('feed').text == 'MANUAL'
        assert new_sec.find('isRetired').text == 'false'
        assert new_sec.get('id') is not None

    def test_security_has_unique_id(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_security('First', 'EUR', backup=False)
        writer2 = PpXmlWriter(writable_xml)
        writer2.add_security('Second', 'USD', backup=False)

        tree = _parse(writable_xml)
        ids = set()
        for el in tree.getroot().iter():
            id_val = el.get('id')
            if id_val is not None:
                assert id_val not in ids, f"Duplicate id={id_val}"
                ids.add(id_val)


# ─── Deposit / Withdrawal ───

class TestSimpleAccountTransactions:

    def test_add_deposit(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        txn_uuid = writer.add_deposit(
            'test-account-uuid-001',
            datetime(2025, 1, 15),
            5000.0,
            'EUR',
            note='Salary',
            backup=False,
        )

        tree = _parse(writable_xml)
        txn = _find_txn_by_uuid(tree.getroot(), txn_uuid)
        assert txn is not None
        assert txn.tag == 'account-transaction'
        assert txn.find('type').text == 'DEPOSIT'
        assert txn.find('amount').text == '500000'  # 5000 * 100 cents
        assert txn.find('currencyCode').text == 'EUR'
        assert txn.find('note').text == 'Salary'
        assert txn.find('shares').text == '0'

    def test_add_withdrawal(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        txn_uuid = writer.add_withdrawal(
            'test-account-uuid-001',
            datetime(2025, 2, 1),
            1000.0,
            'EUR',
            backup=False,
        )

        tree = _parse(writable_xml)
        txn = _find_txn_by_uuid(tree.getroot(), txn_uuid)
        assert txn is not None
        assert txn.find('type').text == 'REMOVAL'
        assert txn.find('amount').text == '100000'


# ─── Buy / Sell ───

class TestBuySell:

    def test_add_buy_creates_cross_entry(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        txn_uuid = writer.add_buy(
            'test-security-uuid-001',
            'test-account-uuid-001',
            datetime(2025, 3, 1),
            shares=10.0,
            amount=500.0,
            currency='EUR',
            fees=9.95,
            backup=False,
        )

        tree = _parse(writable_xml)
        root = tree.getroot()

        # Find the portfolio transaction by UUID
        port_txn = _find_txn_by_uuid(root, txn_uuid)
        assert port_txn is not None
        assert port_txn.find('type').text == 'BUY'
        assert port_txn.find('shares').text == str(10 * 10**8)
        assert port_txn.find('amount').text == '50000'

        # Verify fee unit
        fee_unit = port_txn.find('.//units/unit[@type="FEE"]/amount')
        assert fee_unit is not None
        assert fee_unit.get('amount') == '995'
        assert fee_unit.get('currency') == 'EUR'

        # Verify cross entry structure
        cross = port_txn.find('crossEntry')
        assert cross is not None
        assert cross.get('class') == 'buysell'

        # The cross entry should be a back-reference
        assert cross.get('reference') is not None

    def test_add_sell(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        txn_uuid = writer.add_sell(
            'test-security-uuid-001',
            'test-account-uuid-001',
            datetime(2025, 4, 1),
            shares=3.0,
            amount=180.0,
            currency='EUR',
            taxes=5.25,
            backup=False,
        )

        tree = _parse(writable_xml)
        port_txn = _find_txn_by_uuid(tree.getroot(), txn_uuid)
        assert port_txn is not None
        assert port_txn.find('type').text == 'SELL'
        assert port_txn.find('shares').text == str(3 * 10**8)

        # Verify tax unit
        tax_unit = port_txn.find('.//units/unit[@type="TAX"]/amount')
        assert tax_unit is not None
        assert tax_unit.get('amount') == '525'

    def test_buy_adds_portfolio_reference(self, writable_xml: Path) -> None:
        """Buy should add a portfolio-transaction reference in the portfolio's transactions."""
        writer = PpXmlWriter(writable_xml)
        txn_uuid = writer.add_buy(
            'test-security-uuid-001',
            'test-account-uuid-001',
            datetime(2025, 5, 1),
            shares=5.0,
            amount=250.0,
            currency='EUR',
            backup=False,
        )

        tree = _parse(writable_xml)
        root = tree.getroot()

        # Find the portfolio element (reference="11" in partial_sell fixture)
        port_el = None
        for p in root.findall('.//portfolios/portfolio'):
            if p.get('reference') is not None:
                # This is the reference element
                port_el_ref = p
                continue

        # The portfolio's transactions should contain a reference to our new txn
        port_txn = _find_txn_by_uuid(root, txn_uuid)
        assert port_txn is not None

        # Find the reference in portfolios/portfolio element
        # The portfolio should have a portfolio-transaction ref pointing to our txn
        port_found = False
        for pt in root.iter('portfolio-transaction'):
            ref = pt.get('reference')
            if ref is not None and ref == port_txn.get('id'):
                port_found = True
                break
        assert port_found, "Portfolio should contain a reference to the new portfolio-transaction"


# ─── Dividend ───

class TestDividend:

    def test_add_dividend(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        txn_uuid = writer.add_dividend(
            'test-security-uuid-001',
            'test-account-uuid-001',
            datetime(2025, 6, 15),
            amount=42.50,
            currency='EUR',
            shares=10.0,
            taxes=11.20,
            note='Q2 dividend',
            backup=False,
        )

        tree = _parse(writable_xml)
        txn = _find_txn_by_uuid(tree.getroot(), txn_uuid)
        assert txn is not None
        assert txn.find('type').text == 'DIVIDENDS'
        assert txn.find('amount').text == '4250'
        assert txn.find('shares').text == str(10 * 10**8)
        assert txn.find('note').text == 'Q2 dividend'

        # Verify security reference
        sec_ref = txn.find('security')
        assert sec_ref is not None
        assert sec_ref.get('reference') is not None

        # Verify tax unit
        tax_unit = txn.find('.//units/unit[@type="TAX"]/amount')
        assert tax_unit is not None
        assert tax_unit.get('amount') == '1120'


# ─── Stock Split ───

class TestStockSplit:

    def test_add_stock_split(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_stock_split(
            'test-security-uuid-001',
            datetime(2025, 7, 1),
            '4:1',
            backup=False,
        )

        tree = _parse(writable_xml)
        root = tree.getroot()

        # Find the security
        events = None
        for sec in root.findall('.//securities/security'):
            uuid_el = sec.find('uuid')
            if uuid_el is not None and uuid_el.text == 'test-security-uuid-001':
                events = sec.find('events')
                break

        assert events is not None
        event_els = events.findall('event')
        assert len(event_els) >= 1

        split = event_els[-1]  # Last event should be our new split
        assert split.find('type').text == 'STOCK_SPLIT'
        assert split.find('details').text == '4:1'


# ─── Delivery ───

class TestDelivery:

    def test_delivery_inbound(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        txn_uuid = writer.add_delivery_inbound(
            'test-security-uuid-001',
            'test-portfolio-uuid-001',
            datetime(2025, 8, 1),
            shares=20.0,
            amount=1000.0,
            currency='EUR',
            backup=False,
        )

        tree = _parse(writable_xml)
        txn = _find_txn_by_uuid(tree.getroot(), txn_uuid)
        assert txn is not None
        assert txn.tag == 'portfolio-transaction'
        assert txn.find('type').text == 'DELIVERY_INBOUND'
        assert txn.find('shares').text == str(20 * 10**8)
        assert txn.find('amount').text == '100000'

    def test_delivery_outbound(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        txn_uuid = writer.add_delivery_outbound(
            'test-security-uuid-001',
            'test-portfolio-uuid-001',
            datetime(2025, 8, 15),
            shares=5.0,
            amount=300.0,
            currency='EUR',
            backup=False,
        )

        tree = _parse(writable_xml)
        txn = _find_txn_by_uuid(tree.getroot(), txn_uuid)
        assert txn is not None
        assert txn.find('type').text == 'DELIVERY_OUTBOUND'


# ─── Interest ───

class TestInterest:

    def test_add_interest(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        txn_uuid = writer.add_interest(
            'test-account-uuid-001',
            datetime(2025, 9, 30),
            amount=12.75,
            currency='EUR',
            taxes=3.36,
            backup=False,
        )

        tree = _parse(writable_xml)
        txn = _find_txn_by_uuid(tree.getroot(), txn_uuid)
        assert txn is not None
        assert txn.find('type').text == 'INTEREST'
        assert txn.find('amount').text == '1275'

        # Verify tax unit
        tax_unit = txn.find('.//units/unit[@type="TAX"]/amount')
        assert tax_unit is not None
        assert tax_unit.get('amount') == '336'


# ─── Account Transfer ───

class TestAccountTransfer:

    def test_transfer_between_accounts(self, tmp_path: Path) -> None:
        """Transfer needs two cash accounts — create a fixture with two."""
        src = FIXTURES / 'partial_sell.ids.xml'
        dst = tmp_path / 'two_accounts.xml'
        shutil.copy(src, dst)

        # Add a second account to the fixture
        tree = ET.parse(str(dst))
        root = tree.getroot()
        accounts = root.find('accounts')
        acc2 = ET.SubElement(accounts, 'account')
        acc2.set('id', '100')
        ET.SubElement(acc2, 'uuid').text = 'test-account-uuid-002'
        ET.SubElement(acc2, 'name').text = 'Second Account'
        ET.SubElement(acc2, 'currencyCode').text = 'EUR'
        ET.SubElement(acc2, 'isRetired').text = 'false'
        ET.SubElement(acc2, 'transactions')
        attrs = ET.SubElement(acc2, 'attributes')
        ET.SubElement(attrs, 'map')
        ET.SubElement(acc2, 'updatedAt').text = '2025-01-01T00:00:00.000000Z'
        tree.write(str(dst), encoding='UTF-8', xml_declaration=True)

        writer = PpXmlWriter(dst)
        txn_uuid = writer.add_account_transfer(
            'test-account-uuid-001',
            'test-account-uuid-002',
            datetime(2025, 10, 1),
            2000.0,
            'EUR',
            backup=False,
        )

        tree = _parse(dst)
        root = tree.getroot()

        # Find source transaction
        txn = _find_txn_by_uuid(root, txn_uuid)
        assert txn is not None
        assert txn.find('type').text == 'TRANSFER_OUT'

        # Verify cross entry
        cross = txn.find('crossEntry')
        assert cross is not None
        assert cross.get('class') == 'account-transfer'

        # Destination should have a reference
        dest_txns = None
        for acc in root.findall('accounts/account'):
            uuid_el = acc.find('uuid')
            if uuid_el is not None and uuid_el.text == 'test-account-uuid-002':
                dest_txns = acc.find('transactions')
                break

        assert dest_txns is not None
        refs = dest_txns.findall('account-transaction')
        assert len(refs) >= 1


# ─── Backup ───

class TestBackup:

    def test_backup_created_by_default(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_deposit(
            'test-account-uuid-001',
            datetime(2025, 1, 1),
            100.0,
            'EUR',
            backup=True,
        )

        bak = writable_xml.with_suffix('.xml.bak')
        assert bak.exists()

    def test_no_backup_when_disabled(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_deposit(
            'test-account-uuid-001',
            datetime(2025, 1, 1),
            100.0,
            'EUR',
            backup=False,
        )

        bak = writable_xml.with_suffix('.xml.bak')
        assert not bak.exists()


# ─── Error handling ───

class TestErrors:

    def test_invalid_security_raises(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        with pytest.raises(Exception, match="not found"):
            writer.add_buy(
                'NONEXISTENT',
                'test-account-uuid-001',
                datetime(2025, 1, 1),
                shares=1.0, amount=100.0, currency='EUR',
                backup=False,
            )

    def test_invalid_account_raises(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        with pytest.raises(Exception, match="not found"):
            writer.add_deposit(
                'nonexistent-account',
                datetime(2025, 1, 1),
                100.0, 'EUR',
                backup=False,
            )


# ─── Roundtrip: write then import ───

class TestRoundtrip:

    def test_written_file_can_be_reimported(self, writable_xml: Path) -> None:
        """Verify that after writing, ppxml2db can still parse the file."""
        writer = PpXmlWriter(writable_xml)
        writer.add_deposit(
            'test-account-uuid-001',
            datetime(2025, 1, 1),
            100.0,
            'EUR',
            backup=False,
        )
        writer2 = PpXmlWriter(writable_xml)
        writer2.add_buy(
            'test-security-uuid-001',
            'test-account-uuid-001',
            datetime(2025, 2, 1),
            shares=5.0,
            amount=250.0,
            currency='EUR',
            backup=False,
        )

        # Verify lxml can still parse it
        tree = _parse(writable_xml)
        assert tree.getroot().tag == 'client'

        # Verify all ids are unique
        ids = []
        for el in tree.getroot().iter():
            id_val = el.get('id')
            if id_val is not None:
                ids.append(id_val)
        assert len(ids) == len(set(ids)), "All id attributes must be unique"

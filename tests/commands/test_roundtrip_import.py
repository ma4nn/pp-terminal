"""Round-trip integration test: write via PpXmlWriter, read back via ppxml2db -> PpPortfolioBuilder.

Verifies that every transaction type survives the full write -> parse -> query cycle.
"""

import shutil
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pp_terminal.data.pp_portfolio_builder import PpPortfolioBuilder
from pp_terminal.data.xml_writer import PpXmlWriter
from pp_terminal.domain.schemas import TransactionType, AccountType
from pp_terminal.main import app

FIXTURES = Path(__file__).parent.parent / 'fixtures'


@pytest.fixture
def writable_xml(tmp_path: Path) -> Path:
    src = FIXTURES / 'partial_sell.ids.xml'
    dst = tmp_path / 'roundtrip.xml'
    shutil.copy(src, dst)
    return dst


def _build_portfolio(xml_path: Path):
    """Parse an XML file through the full ppxml2db -> Portfolio pipeline."""
    builder = PpPortfolioBuilder()
    return builder.construct(xml_path)


def _find_txn(portfolio, txn_type: str, date_str: str, security_uuid: str | None = None):
    """Find a transaction in the portfolio by type and date.

    Uses securities_account_transactions for security-related types,
    deposit_account_transactions for cash-only types.
    """
    if security_uuid:
        txns = portfolio.securities_account_transactions.reset_index()
    else:
        txns = portfolio.deposit_account_transactions.reset_index()

    target_date = datetime.fromisoformat(date_str)
    mask = (txns['type'] == txn_type) & (txns['date'].dt.date == target_date.date())
    matched = txns[mask]

    if len(matched) == 0:
        return None
    return matched.iloc[0]


class TestRoundTripDeposit:

    def test_deposit_appears_in_transactions(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_deposit(
            'test-account-uuid-001',
            datetime(2025, 6, 1),
            5000.0,
            'EUR',
            note='Round-trip deposit',
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        txn = _find_txn(portfolio, TransactionType.DEPOSIT.value, '2025-06-01')
        assert txn is not None
        assert abs(txn['amount'] - 5000.0) < 0.01
        assert txn['currency'] == 'EUR'


class TestRoundTripWithdrawal:

    def test_withdrawal_appears_in_transactions(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_withdrawal(
            'test-account-uuid-001',
            datetime(2025, 6, 15),
            1200.0,
            'EUR',
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        txn = _find_txn(portfolio, TransactionType.REMOVAL.value, '2025-06-15')
        assert txn is not None
        # Removal amounts are negative in the parsed DataFrame
        assert abs(txn['amount'] - (-1200.0)) < 0.01


class TestRoundTripBuy:

    def test_buy_roundtrip(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_buy(
            'test-security-uuid-001',
            'test-account-uuid-001',
            datetime(2025, 7, 1),
            shares=15.0,
            amount=750.0,
            currency='EUR',
            fees=9.95,
            taxes=2.50,
            note='Test buy',
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        txn = _find_txn(portfolio, TransactionType.BUY.value, '2025-07-01',
                         security_uuid='test-security-uuid-001')
        assert txn is not None
        assert abs(txn['shares'] - 15.0) < 0.0001
        assert abs(txn['fees'] - 9.95) < 0.01
        assert abs(txn['taxes'] - 2.50) < 0.01
        assert txn['currency'] == 'EUR'


class TestRoundTripSell:

    def test_sell_roundtrip(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_sell(
            'test-security-uuid-001',
            'test-account-uuid-001',
            datetime(2025, 8, 1),
            shares=3.0,
            amount=180.0,
            currency='EUR',
            taxes=12.50,
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        txn = _find_txn(portfolio, TransactionType.SELL.value, '2025-08-01',
                         security_uuid='test-security-uuid-001')
        assert txn is not None
        assert abs(txn['shares'] - 3.0) < 0.0001
        assert abs(txn['taxes'] - 12.50) < 0.01


class TestRoundTripDividend:

    def test_dividend_roundtrip(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_dividend(
            'test-security-uuid-001',
            'test-account-uuid-001',
            datetime(2025, 9, 15),
            amount=42.50,
            currency='EUR',
            shares=10.0,
            taxes=11.20,
            note='Q3 dividend',
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        txn = _find_txn(portfolio, TransactionType.DIVIDENDS.value, '2025-09-15')
        assert txn is not None
        assert abs(txn['amount'] - 42.50) < 0.01
        assert abs(txn['taxes'] - 11.20) < 0.01
        assert abs(txn['shares'] - 10.0) < 0.0001


class TestRoundTripInterest:

    def test_interest_roundtrip(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_interest(
            'test-account-uuid-001',
            datetime(2025, 12, 31),
            amount=25.75,
            currency='EUR',
            taxes=6.80,
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        txn = _find_txn(portfolio, TransactionType.INTEREST.value, '2025-12-31')
        assert txn is not None
        assert abs(txn['amount'] - 25.75) < 0.01
        assert abs(txn['taxes'] - 6.80) < 0.01


class TestRoundTripDelivery:

    def test_delivery_inbound_roundtrip(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_delivery_inbound(
            'test-security-uuid-001',
            'test-portfolio-uuid-001',
            datetime(2025, 10, 1),
            shares=20.0,
            amount=1000.0,
            currency='EUR',
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        txn = _find_txn(portfolio, TransactionType.DELIVERY_INBOUND.value, '2025-10-01',
                         security_uuid='test-security-uuid-001')
        assert txn is not None
        assert abs(txn['shares'] - 20.0) < 0.0001

    def test_delivery_outbound_roundtrip(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        writer.add_delivery_outbound(
            'test-security-uuid-001',
            'test-portfolio-uuid-001',
            datetime(2025, 10, 15),
            shares=5.0,
            amount=300.0,
            currency='EUR',
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        txn = _find_txn(portfolio, TransactionType.DELIVERY_OUTBOUND.value, '2025-10-15',
                         security_uuid='test-security-uuid-001')
        assert txn is not None
        assert abs(txn['shares'] - 5.0) < 0.0001


class TestRoundTripStockSplit:

    def test_stock_split_roundtrip(self, writable_xml: Path) -> None:
        """Stock splits are stored as security events, not transactions."""
        from pp_terminal.data.ppxml2db_wrapper import Ppxml2dbWrapper, DB_NAME_IN_MEMORY

        writer = PpXmlWriter(writable_xml)
        writer.add_stock_split(
            'test-security-uuid-001',
            datetime(2025, 11, 1),
            '4:1',
            backup=False,
        )

        wrapper = Ppxml2dbWrapper(dbname=DB_NAME_IN_MEMORY)
        wrapper.open(writable_xml)

        cursor = wrapper.connection.cursor()
        cursor.execute(
            "SELECT type, details FROM security_event WHERE security = ?",
            ('test-security-uuid-001',)
        )
        events = cursor.fetchall()
        wrapper.close()

        assert any(ev[0] == 'STOCK_SPLIT' and ev[1] == '4:1' for ev in events)


class TestRoundTripAccountTransfer:

    def test_transfer_roundtrip(self, writable_xml: Path) -> None:
        """Account transfers need a second cash account.

        The new account must appear *before* the existing one in the XML so
        that ppxml2db's iterparse has already mapped its id when it encounters
        the cross-entry ``accountTo reference``.
        """
        import lxml.etree as ET

        tree = ET.parse(str(writable_xml))
        root = tree.getroot()
        accounts = root.find('accounts')
        acc2 = ET.Element('account')
        acc2.set('id', '100')
        accounts.insert(0, acc2)  # before existing account
        ET.SubElement(acc2, 'uuid').text = 'test-account-uuid-002'
        ET.SubElement(acc2, 'name').text = 'Second Account'
        ET.SubElement(acc2, 'currencyCode').text = 'EUR'
        ET.SubElement(acc2, 'isRetired').text = 'false'
        ET.SubElement(acc2, 'transactions')
        attrs = ET.SubElement(acc2, 'attributes')
        ET.SubElement(attrs, 'map')
        ET.SubElement(acc2, 'updatedAt').text = '2025-01-01T00:00:00.000000Z'
        tree.write(str(writable_xml), encoding='UTF-8', xml_declaration=True)

        writer = PpXmlWriter(writable_xml)
        writer.add_account_transfer(
            'test-account-uuid-001',
            'test-account-uuid-002',
            datetime(2025, 12, 1),
            2000.0,
            'EUR',
            note='Transfer test',
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        txn = _find_txn(portfolio, TransactionType.TRANSFER_OUT.value, '2025-12-01')
        assert txn is not None
        # TRANSFER_OUT amount is negative
        assert abs(txn['amount'] - (-2000.0)) < 0.01


class TestRoundTripSecurity:

    def test_new_security_appears_after_roundtrip(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        sec_uuid = writer.add_security(
            'Round-trip Test ETF',
            'USD',
            isin='US0000000001',
            ticker='RT.US',
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)
        secs = portfolio.securities
        assert sec_uuid in secs.index
        assert secs.loc[sec_uuid, 'name'] == 'Round-trip Test ETF'
        assert secs.loc[sec_uuid, 'currency'] == 'USD'
        assert secs.loc[sec_uuid, 'isin'] == 'US0000000001'


class TestFullWorkflowRoundTrip:
    """End-to-end: add a new security, deposit cash, buy shares, then verify
    the complete portfolio state through the CLI."""

    def test_full_workflow(self, writable_xml: Path) -> None:
        writer = PpXmlWriter(writable_xml)
        new_sec_uuid = writer.add_security(
            'New Corp Inc',
            'EUR',
            isin='DE9999999999',
            backup=False,
        )

        writer2 = PpXmlWriter(writable_xml)
        writer2.add_deposit(
            'test-account-uuid-001',
            datetime(2025, 1, 1),
            100000.0,
            'EUR',
            backup=False,
        )

        writer3 = PpXmlWriter(writable_xml)
        writer3.add_buy(
            new_sec_uuid,
            'test-account-uuid-001',
            datetime(2025, 2, 1),
            shares=50.0,
            amount=5000.0,
            currency='EUR',
            fees=15.0,
            backup=False,
        )

        portfolio = _build_portfolio(writable_xml)

        # New security exists
        assert new_sec_uuid in portfolio.securities.index
        assert portfolio.securities.loc[new_sec_uuid, 'name'] == 'New Corp Inc'

        # The buy transaction for the new security exists
        txn = _find_txn(portfolio, TransactionType.BUY.value, '2025-02-01',
                         security_uuid=new_sec_uuid)
        assert txn is not None
        assert abs(txn['shares'] - 50.0) < 0.0001
        assert abs(txn['fees'] - 15.0) < 0.01

    def test_cli_view_after_writes(self, writable_xml: Path) -> None:
        """Verify the CLI can read back written data without errors."""
        writer = PpXmlWriter(writable_xml)
        writer.add_deposit(
            'test-account-uuid-001',
            datetime(2025, 5, 1),
            3000.0,
            'EUR',
            backup=False,
        )

        runner = CliRunner()
        result = runner.invoke(app, [
            '--file', str(writable_xml),
            '--output', 'json',
            '--no-cache',
            'view', 'accounts',
        ])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        import json
        data = json.loads(result.stdout)
        assert len(data) >= 1

        acc = next(a for a in data if a.get('name') == 'Test Cash Account')
        assert acc is not None

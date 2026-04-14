"""
    Copyright (C) 2025-26 Daniel Gehriger

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

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import lxml.etree as ET  # pylint: disable=c-extension-no-member

from pp_terminal.exceptions import InputError

# Transaction functions unavoidably take many parameters (date, amount,
# currency, fees, taxes, note, …).  Suppress the corresponding refactor
# messages for the whole module rather than annotating every function.
# pylint: disable=too-many-arguments,too-many-positional-arguments

log = logging.getLogger(__name__)

# PP stores monetary amounts in 1/100 of the base unit (cents)
_CENTS = 100
# PP stores share counts scaled by 10^8
_SHARE_SCALE = 10**8


class PpXmlWriter:
    """Writes transactions and securities into a Portfolio Performance XML file.

    Operates directly on the XML DOM via lxml, preserving existing structure
    and references. All modifications are written back to the file atomically.
    """

    def __init__(self, file_path: Path):
        self._file_path = file_path
        self._parser = ET.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True)
        self._tree: ET.ElementTree | None = None
        self._root: ET.Element | None = None
        self._id_counter: int | None = None

    def _load(self) -> None:
        self._tree = ET.parse(str(self._file_path), self._parser)
        self._root = self._tree.getroot()

    def _save(self, backup: bool = True) -> None:
        if backup:
            backup_path = self._file_path.with_suffix(self._file_path.suffix + '.bak')
            shutil.copy2(self._file_path, backup_path)
            log.info("Backup saved to %s", backup_path)

        self._tree.write(
            str(self._file_path),
            encoding='UTF-8',
            xml_declaration=True,
            pretty_print=False,
        )

    def _next_id(self) -> int:
        """Find the maximum id attribute in the document and return max+1."""
        max_id = 0
        for el in self._root.iter():
            id_val = el.get('id')
            if id_val is not None:
                try:
                    max_id = max(max_id, int(id_val))
                except ValueError:
                    pass
        return max_id + 1

    def _alloc_id(self) -> str:
        """Allocate a new unique integer id, tracking allocations within a session."""
        if self._id_counter is None:
            self._id_counter = self._next_id()
        id_str = str(self._id_counter)
        self._id_counter += 1
        return id_str

    @staticmethod
    def _gen_uuid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

    @staticmethod
    def _date_iso(dt: datetime) -> str:
        return dt.strftime('%Y-%m-%dT00:00')

    @staticmethod
    def _amount_raw(value: float) -> str:
        """Convert a decimal amount (e.g. 100.50) to PP internal integer (10050)."""
        return str(round(value * _CENTS))

    @staticmethod
    def _shares_raw(shares: float) -> str:
        """Convert a share count to PP internal scaled integer."""
        return str(round(shares * _SHARE_SCALE))

    def _find_security_el(self, security_id: str) -> ET.Element:
        """Find a <security> element by UUID or ISIN."""
        for sec in self._root.findall('.//securities/security'):
            if sec.get('reference') is not None:
                continue
            uuid_el = sec.find('uuid')
            if uuid_el is not None and uuid_el.text == security_id:
                return sec
            isin_el = sec.find('isin')
            if isin_el is not None and isin_el.text == security_id:
                return sec
        raise InputError(f"Security '{security_id}' not found")

    def _find_account_el(self, account_id: str, account_type: str = 'account') -> ET.Element:
        """Find an <account> or <portfolio> element by UUID.

        Iterates the entire tree because PP XML (serialised by XStream) may
        define the canonical element under any tag name — for instance the
        first occurrence of an account might be ``<accountTo>`` inside a
        crossEntry, with the ``<account>`` entries in ``<accounts>`` being
        mere references.  Therefore we search **all** elements by UUID.
        """
        for el in self._root.iter():
            if el.get('reference') is not None:
                continue
            uuid_el = el.find('uuid')
            if uuid_el is not None and uuid_el.text == account_id:
                return el
        raise InputError(f"Account '{account_id}' not found (type={account_type})")

    def _find_portfolio_for_account(self, account_id: str) -> ET.Element:
        """Find the portfolio that references the given cash account.

        Searches all elements because XStream may define the canonical
        portfolio under a tag other than ``<portfolio>``.
        """
        acc_el = self._find_account_el(account_id, 'account')
        acc_xml_id = acc_el.get('id')

        for el in self._root.iter():
            if el.get('reference') is not None:
                continue
            ref_acc = el.find('referenceAccount')
            if ref_acc is None:
                continue
            if ref_acc.get('reference') is not None:
                if ref_acc.get('reference') == acc_xml_id:
                    return el
            else:
                ref_uuid = ref_acc.find('uuid')
                if ref_uuid is not None and ref_uuid.text == account_id:
                    return el
        raise InputError(f"No portfolio linked to cash account '{account_id}'")

    def _find_el_by_xmlid(self, xml_id: str) -> ET.Element | None:
        """Find any element by its id attribute value."""
        for el in self._root.iter():
            if el.get('id') == xml_id:
                return el
        return None

    def _make_sub(self, parent: ET.Element, tag: str, text: str | None = None,
                  attribs: dict | None = None, with_id: bool = False) -> ET.Element:
        el = ET.SubElement(parent, tag)
        if with_id:
            el.set('id', self._alloc_id())
        if attribs:
            for k, v in attribs.items():
                el.set(k, str(v))
        if text is not None:
            el.text = text
        return el

    def _make_unit(self, parent: ET.Element, unit_type: str, amount: float, currency: str) -> None:
        unit = self._make_sub(parent, 'unit', attribs={'type': unit_type})
        self._make_sub(unit, 'amount', attribs={'currency': currency, 'amount': self._amount_raw(amount)})

    def _get_transactions_el(self, parent: ET.Element) -> ET.Element:
        txns = parent.find('transactions')
        if txns is None:
            txns = self._make_sub(parent, 'transactions')
        return txns

    # ─── Public API ───

    def delete_transaction(self, txn_uuid: str, backup: bool = True) -> dict:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        """Delete a transaction by UUID. Handles cross-entry cleanup for transfers.

        Returns a dict with details of what was deleted.
        """
        self._load()

        # Find the element that has this UUID
        txn_el = None
        for el in self._root.iter():
            uuid_sub = el.find('uuid')
            if uuid_sub is not None and uuid_sub.text == txn_uuid:
                txn_el = el
                break

        if txn_el is None:
            raise InputError(f"Transaction with UUID '{txn_uuid}' not found")

        ttype = txn_el.findtext('type', '?')
        result: dict = {'uuid': txn_uuid, 'type': ttype, 'deleted': []}

        # Simple (non-transfer) account transaction or portfolio transaction
        if ttype not in ('TRANSFER_IN', 'TRANSFER_OUT'):
            parent = txn_el.getparent()
            if parent is not None:
                parent.remove(txn_el)
                result['deleted'].append(ttype)
            self._save(backup=backup)
            return result

        # Transfer: need to remove both sides and the cross-entry
        # Find the crossEntry from either the outer element or the inner element
        cross = txn_el.find('crossEntry')
        if cross is None:
            # Inner transaction — crossEntry is referenced, find it
            cross_ref = txn_el.find('crossEntry')
            if cross_ref is not None:
                ref_id = cross_ref.get('reference')
                if ref_id:
                    cross = self._find_el_by_xmlid(ref_id)

        if cross is None:
            # Fallback: just remove this element
            parent = txn_el.getparent()
            if parent is not None:
                parent.remove(txn_el)
                result['deleted'].append(ttype)
            self._save(backup=backup)
            return result

        # Collect all XML IDs in this transfer cluster for reference cleanup
        ids_to_remove: set[str] = set()
        for el in [txn_el] + list(cross):
            eid = el.get('id')
            if eid:
                ids_to_remove.add(eid)
        cross_id = cross.get('id')
        if cross_id:
            ids_to_remove.add(cross_id)

        # Find the outer transaction (parent of crossEntry)
        outer_txn = cross.getparent()
        outer_id = outer_txn.get('id') if outer_txn is not None else None
        if outer_id:
            ids_to_remove.add(outer_id)

        # Remove all reference elements pointing to any of our IDs
        to_remove = []
        for el in self._root.iter():
            ref = el.get('reference')
            if ref and ref in ids_to_remove:
                to_remove.append(el)

        for el in to_remove:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
                result['deleted'].append(f"ref({el.tag})")

        # Remove the outer transaction (which contains crossEntry)
        if outer_txn is not None:
            outer_parent = outer_txn.getparent()
            if outer_parent is not None:
                outer_parent.remove(outer_txn)
                result['deleted'].append(outer_txn.findtext('type', 'outer'))

        self._save(backup=backup)
        return result

    def add_security(
        self,
        name: str,
        currency: str,
        isin: str | None = None,
        wkn: str | None = None,
        ticker: str | None = None,
        feed: str = 'MANUAL',
        backup: bool = True,
    ) -> str:
        """Add a new security definition. Returns the new security UUID."""
        self._load()

        sec_uuid = self._gen_uuid()
        now = self._now_iso()
        securities = self._root.find('securities')
        if securities is None:
            raise InputError("Invalid PP XML: missing <securities> element")

        sec = self._make_sub(securities, 'security', with_id=True)
        self._make_sub(sec, 'uuid', sec_uuid)
        self._make_sub(sec, 'name', name)
        self._make_sub(sec, 'currencyCode', currency)
        if isin:
            self._make_sub(sec, 'isin', isin)
        if ticker:
            self._make_sub(sec, 'tickerSymbol', ticker)
        if wkn:
            self._make_sub(sec, 'wkn', wkn)
        self._make_sub(sec, 'feed', feed)
        self._make_sub(sec, 'prices')
        self._make_sub(sec, 'events')
        attrs = self._make_sub(sec, 'attributes')
        self._make_sub(attrs, 'map')
        self._make_sub(sec, 'isRetired', 'false')
        self._make_sub(sec, 'updatedAt', now)

        self._save(backup=backup)
        log.info("Added security '%s' (uuid=%s)", name, sec_uuid)
        return sec_uuid

    def add_buy(
        self,
        security: str,
        account_id: str,
        date: datetime,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
        portfolio_id: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a BUY transaction. Returns the portfolio transaction UUID.

        Creates the cross-linked portfolio-transaction + account-transaction pair.
        When the cash account is not the portfolio's referenceAccount (e.g.
        multi-currency setups), pass *portfolio_id* explicitly.
        """
        return self._add_buysell(
            'BUY', security, account_id, date, shares, amount, currency,
            fees=fees, taxes=taxes, note=note, portfolio_id=portfolio_id, backup=backup,
        )

    def add_sell(
        self,
        security: str,
        account_id: str,
        date: datetime,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
        portfolio_id: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a SELL transaction. Returns the portfolio transaction UUID."""
        return self._add_buysell(
            'SELL', security, account_id, date, shares, amount, currency,
            fees=fees, taxes=taxes, note=note, portfolio_id=portfolio_id, backup=backup,
        )

    def add_dividend(  # pylint: disable=too-many-locals
        self,
        security: str,
        account_id: str,
        date: datetime,
        amount: float,
        currency: str,
        shares: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a DIVIDENDS transaction on a cash account. Returns the transaction UUID."""
        self._load()

        sec_el = self._find_security_el(security)
        acc_el = self._find_account_el(account_id, 'account')
        txns = self._get_transactions_el(acc_el)

        txn_uuid = self._gen_uuid()
        now = self._now_iso()

        txn = self._make_sub(txns, 'account-transaction', with_id=True)
        self._make_sub(txn, 'uuid', txn_uuid)
        self._make_sub(txn, 'date', self._date_iso(date))
        self._make_sub(txn, 'currencyCode', currency)
        self._make_sub(txn, 'amount', self._amount_raw(amount))
        self._make_sub(txn, 'security', attribs={'reference': sec_el.get('id')})
        self._make_sub(txn, 'shares', self._shares_raw(shares))

        if taxes > 0:
            units = self._make_sub(txn, 'units')
            self._make_unit(units, 'TAX', taxes, currency)

        self._make_sub(txn, 'updatedAt', now)
        self._make_sub(txn, 'type', 'DIVIDENDS')
        if note:
            self._make_sub(txn, 'note', note)

        self._save(backup=backup)
        log.info("Added DIVIDENDS transaction (uuid=%s)", txn_uuid)
        return txn_uuid

    def add_deposit(
        self,
        account_id: str,
        date: datetime,
        amount: float,
        currency: str,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a DEPOSIT transaction. Returns the transaction UUID."""
        return self._add_simple_account_txn(
            'DEPOSIT', account_id, date, amount, currency, note=note, backup=backup,
        )

    def add_withdrawal(
        self,
        account_id: str,
        date: datetime,
        amount: float,
        currency: str,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a REMOVAL transaction. Returns the transaction UUID."""
        return self._add_simple_account_txn(
            'REMOVAL', account_id, date, amount, currency, note=note, backup=backup,
        )

    def add_stock_split(
        self,
        security: str,
        date: datetime,
        ratio: str,
        backup: bool = True,
    ) -> None:
        """Add a stock split event to a security.

        Args:
            security: ISIN or UUID of the security
            date: Date of the split
            ratio: Split ratio as string, e.g. '4:1' for a 4-for-1 split
            backup: Create .bak backup before writing
        """
        self._load()
        sec_el = self._find_security_el(security)

        events = sec_el.find('events')
        if events is None:
            events = self._make_sub(sec_el, 'events')

        event = self._make_sub(events, 'event')
        self._make_sub(event, 'date', self._date_iso(date))
        self._make_sub(event, 'type', 'STOCK_SPLIT')
        self._make_sub(event, 'details', ratio)

        self._save(backup=backup)
        log.info("Added STOCK_SPLIT event for security '%s' (ratio=%s)", security, ratio)

    def add_delivery_inbound(
        self,
        security: str,
        portfolio_id: str,
        date: datetime,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a DELIVERY_INBOUND transaction (shares delivered into a portfolio without a cash leg).
        Returns the portfolio transaction UUID."""
        return self._add_delivery(
            'DELIVERY_INBOUND', security, portfolio_id, date, shares, amount, currency,
            fees=fees, taxes=taxes, note=note, backup=backup,
        )

    def add_delivery_outbound(
        self,
        security: str,
        portfolio_id: str,
        date: datetime,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a DELIVERY_OUTBOUND transaction (shares removed from a portfolio without a cash leg).
        Returns the portfolio transaction UUID."""
        return self._add_delivery(
            'DELIVERY_OUTBOUND', security, portfolio_id, date, shares, amount, currency,
            fees=fees, taxes=taxes, note=note, backup=backup,
        )

    def add_interest(
        self,
        account_id: str,
        date: datetime,
        amount: float,
        currency: str,
        taxes: float = 0.0,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add an INTEREST transaction on a cash account. Returns the transaction UUID."""
        self._load()
        acc_el = self._find_account_el(account_id, 'account')
        txns = self._get_transactions_el(acc_el)

        txn_uuid = self._gen_uuid()
        now = self._now_iso()

        txn = self._make_sub(txns, 'account-transaction', with_id=True)
        self._make_sub(txn, 'uuid', txn_uuid)
        self._make_sub(txn, 'date', self._date_iso(date))
        self._make_sub(txn, 'currencyCode', currency)
        self._make_sub(txn, 'amount', self._amount_raw(amount))
        self._make_sub(txn, 'shares', '0')

        if taxes > 0:
            units = self._make_sub(txn, 'units')
            self._make_unit(units, 'TAX', taxes, currency)

        self._make_sub(txn, 'updatedAt', now)
        self._make_sub(txn, 'type', 'INTEREST')
        if note:
            self._make_sub(txn, 'note', note)

        self._save(backup=backup)
        log.info("Added INTEREST transaction (uuid=%s)", txn_uuid)
        return txn_uuid

    def add_fees(
        self,
        account_id: str,
        date: datetime,
        amount: float,
        currency: str,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a FEES transaction on a cash account. Returns the transaction UUID."""
        return self._add_simple_account_txn(
            'FEES', account_id, date, amount, currency, note=note, backup=backup,
        )

    def add_taxes(
        self,
        account_id: str,
        date: datetime,
        amount: float,
        currency: str,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a TAXES transaction on a cash account. Returns the transaction UUID."""
        return self._add_simple_account_txn(
            'TAXES', account_id, date, amount, currency, note=note, backup=backup,
        )

    def add_account_transfer(  # pylint: disable=too-many-locals
        self,
        from_account_id: str,
        to_account_id: str,
        date: datetime,
        amount: float,
        currency: str,
        to_amount: float | None = None,
        to_currency: str | None = None,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add an account-to-account transfer. Returns the source transaction UUID.

        The canonical structure mirrors PP's own XStream serialisation:
        the outer ``account-transaction`` (TRANSFER_IN) lives in the
        **destination** account, its nested ``crossEntry`` holds a canonical
        ``transactionFrom`` (TRANSFER_OUT) with a back-reference to the
        crossEntry, and the source account gets a bare reference element
        pointing at ``transactionFrom``.

        For cross-currency transfers, provide *to_amount* and *to_currency*
        for the destination side; *amount*/*currency* are used for the source.
        """
        self._load()

        from_acc = self._find_account_el(from_account_id, 'account')
        to_acc = self._find_account_el(to_account_id, 'account')

        from_txn_uuid = self._gen_uuid()
        to_txn_uuid = self._gen_uuid()
        now = self._now_iso()

        from_txns = self._get_transactions_el(from_acc)
        to_txns = self._get_transactions_el(to_acc)

        # Resolve destination amount/currency (defaults to same as source)
        dest_amount = to_amount if to_amount is not None else amount
        dest_currency = to_currency if to_currency is not None else currency

        # Destination transaction (TRANSFER_IN) — canonical outer element
        to_txn = self._make_sub(to_txns, 'account-transaction', with_id=True)
        self._make_sub(to_txn, 'uuid', to_txn_uuid)
        self._make_sub(to_txn, 'date', self._date_iso(date))
        self._make_sub(to_txn, 'currencyCode', dest_currency)
        self._make_sub(to_txn, 'amount', self._amount_raw(dest_amount))

        # Cross entry (nested inside the destination transaction)
        cross = self._make_sub(to_txn, 'crossEntry', attribs={'class': 'account-transfer'}, with_id=True)
        self._make_sub(cross, 'accountFrom', attribs={'reference': from_acc.get('id')})

        # Source transaction (TRANSFER_OUT) — canonical, nested in crossEntry
        from_txn = self._make_sub(cross, 'transactionFrom', with_id=True)
        self._make_sub(from_txn, 'uuid', from_txn_uuid)
        self._make_sub(from_txn, 'date', self._date_iso(date))
        self._make_sub(from_txn, 'currencyCode', currency)
        self._make_sub(from_txn, 'amount', self._amount_raw(amount))
        # Back-reference from inner transaction to parent crossEntry
        self._make_sub(from_txn, 'crossEntry', attribs={
            'class': 'account-transfer', 'reference': cross.get('id'),
        })
        self._make_sub(from_txn, 'shares', '0')
        self._make_sub(from_txn, 'updatedAt', now)
        self._make_sub(from_txn, 'type', 'TRANSFER_OUT')
        if note:
            self._make_sub(from_txn, 'note', note)

        self._make_sub(cross, 'accountTo', attribs={'reference': to_acc.get('id')})
        # Back-reference from crossEntry to outer destination transaction
        self._make_sub(cross, 'transactionTo', attribs={'reference': to_txn.get('id')})

        self._make_sub(to_txn, 'shares', '0')
        self._make_sub(to_txn, 'updatedAt', now)
        self._make_sub(to_txn, 'type', 'TRANSFER_IN')
        if note:
            self._make_sub(to_txn, 'note', note)

        # Reference from source account's transactions list to transactionFrom
        self._make_sub(from_txns, 'account-transaction',
                       attribs={'reference': from_txn.get('id')})

        self._save(backup=backup)
        log.info("Added ACCOUNT_TRANSFER (uuid=%s)", from_txn_uuid)
        return from_txn_uuid

    # ─── Private helpers ───

    def _add_simple_account_txn(
        self,
        txn_type: str,
        account_id: str,
        date: datetime,
        amount: float,
        currency: str,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        self._load()

        acc_el = self._find_account_el(account_id, 'account')
        txns = self._get_transactions_el(acc_el)

        txn_uuid = self._gen_uuid()
        now = self._now_iso()

        txn = self._make_sub(txns, 'account-transaction', with_id=True)
        self._make_sub(txn, 'uuid', txn_uuid)
        self._make_sub(txn, 'date', self._date_iso(date))
        self._make_sub(txn, 'currencyCode', currency)
        self._make_sub(txn, 'amount', self._amount_raw(amount))
        self._make_sub(txn, 'shares', '0')
        self._make_sub(txn, 'updatedAt', now)
        self._make_sub(txn, 'type', txn_type)
        if note:
            self._make_sub(txn, 'note', note)

        self._save(backup=backup)
        log.info("Added %s transaction (uuid=%s)", txn_type, txn_uuid)
        return txn_uuid

    def _is_ancestor_of(self, potential_ancestor: ET.Element, element: ET.Element) -> bool:
        """Return True if *potential_ancestor* is an ancestor of *element*."""
        parent = element.getparent()
        while parent is not None:
            if parent is potential_ancestor:
                return True
            parent = parent.getparent()
        return False

    def _pt_definition_in_portfolio(
        self,
        port_el: ET.Element,
        acc_el: ET.Element,
        port_txns: ET.Element,
        acc_txns: ET.Element,
    ) -> bool:
        """Decide where the canonical portfolio-transaction definition goes.

        XStream's ``ID_REFERENCES`` mode requires every ``id="N"``
        definition to appear *before* any ``reference="N"`` in document
        order.  For a BUY/SELL we must place one full definition in either
        the portfolio's or the account's ``<transactions>`` list, and only
        a back-reference in the other.

        Whichever list's **closing tag** comes first in the serialised XML
        receives the full definition; the other list receives the
        reference.  For the common nesting patterns in PP files:

        * **Account contains portfolio** (e.g. CHF account whose first transaction's
          crossEntry defines the portfolio inline) — the portfolio's list
          closes first → definition goes in the portfolio (return True).
        * **Portfolio contains account** (e.g. a USD deposit account first
          encountered inside a crossEntry nested in the portfolio's
          transaction list) — the account's list closes first → definition
          goes in the account (return False).
        * **Neither nested** — whichever ``<transactions>`` element appears
          first in document order also closes first.
        """
        if self._is_ancestor_of(acc_el, port_el):
            return True
        if self._is_ancestor_of(port_el, acc_el):
            return False
        # Neither nested: the element encountered first in a depth-first
        # traversal also closes first.
        for el in self._root.iter():
            if el is port_txns:
                return True
            if el is acc_txns:
                return False
        return True  # fallback

    def _add_buysell_units(self, parent: ET.Element, fees: float, taxes: float, currency: str) -> None:
        """Append ``<units>`` with optional FEE / TAX children to *parent*."""
        if fees > 0 or taxes > 0:
            units = self._make_sub(parent, 'units')
            if fees > 0:
                self._make_unit(units, 'FEE', fees, currency)
            if taxes > 0:
                self._make_unit(units, 'TAX', taxes, currency)
        else:
            self._make_sub(parent, 'units')

    def _add_buysell(  # pylint: disable=too-many-locals,too-many-statements
        self,
        txn_type: str,
        security: str,
        account_id: str,
        date: datetime,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
        portfolio_id: str | None = None,
        backup: bool = True,
    ) -> str:
        """Create a BUY or SELL — the paired portfolio + account transaction.

        Mirrors PP's XStream serialisation: the canonical definition of
        the full ``crossEntry`` graph (portfolio-transaction **and**
        account-transaction) is placed in whichever ``<transactions>``
        list closes first in document order.  The other list receives
        only a ``reference="…"`` back-reference.  This guarantees that
        every ``id="N"`` definition precedes any ``reference="N"`` in
        the serialised XML, as required by XStream's ``ID_REFERENCES``
        mode.
        """
        self._load()

        sec_el = self._find_security_el(security)
        acc_el = self._find_account_el(account_id, 'account')
        if portfolio_id:
            port_el = self._find_account_el(portfolio_id, 'portfolio')
        else:
            port_el = self._find_portfolio_for_account(account_id)

        port_txns = self._get_transactions_el(port_el)
        acc_txns = self._get_transactions_el(acc_el)

        port_txn_uuid = self._gen_uuid()
        acc_txn_uuid = self._gen_uuid()
        now = self._now_iso()
        date_str = self._date_iso(date)
        amount_str = self._amount_raw(amount)
        sec_ref = sec_el.get('id')

        pt_first = self._pt_definition_in_portfolio(port_el, acc_el, port_txns, acc_txns)

        if pt_first:
            # ── Pattern A: portfolio-transaction is the canonical definition ──
            # Matches PP's layout when the account contains the portfolio
            # (e.g. CHF account whose crossEntry defines the portfolio inline).
            pt = self._make_sub(port_txns, 'portfolio-transaction', with_id=True)
            self._make_sub(pt, 'uuid', port_txn_uuid)
            self._make_sub(pt, 'date', date_str)
            self._make_sub(pt, 'currencyCode', currency)
            self._make_sub(pt, 'amount', amount_str)
            self._make_sub(pt, 'security', attribs={'reference': sec_ref})

            ce = self._make_sub(pt, 'crossEntry', attribs={'class': 'buysell'}, with_id=True)
            self._make_sub(ce, 'portfolio', attribs={'reference': port_el.get('id')})
            self._make_sub(ce, 'portfolioTransaction', attribs={'reference': pt.get('id')})
            self._make_sub(ce, 'account', attribs={'reference': acc_el.get('id')})

            at = self._make_sub(ce, 'accountTransaction', with_id=True)
            self._make_sub(at, 'uuid', acc_txn_uuid)
            self._make_sub(at, 'date', date_str)
            self._make_sub(at, 'currencyCode', currency)
            self._make_sub(at, 'amount', amount_str)
            self._make_sub(at, 'security', attribs={'reference': sec_ref})
            self._make_sub(at, 'crossEntry', attribs={'class': 'buysell', 'reference': ce.get('id')})
            self._make_sub(at, 'shares', '0')
            self._make_sub(at, 'updatedAt', now)
            self._make_sub(at, 'type', txn_type)
            if note:
                self._make_sub(at, 'note', note)

            self._make_sub(pt, 'shares', self._shares_raw(shares))
            self._add_buysell_units(pt, fees, taxes, currency)
            self._make_sub(pt, 'updatedAt', now)
            self._make_sub(pt, 'type', txn_type)

            self._make_sub(acc_txns, 'account-transaction',
                           attribs={'reference': at.get('id')})
        else:
            # ── Pattern B: account-transaction is the canonical definition ──
            # Matches PP's layout when the portfolio contains the account
            # (e.g. a USD deposit account first defined inside a crossEntry
            # nested in the portfolio's transaction list).
            at = self._make_sub(acc_txns, 'account-transaction', with_id=True)
            self._make_sub(at, 'uuid', acc_txn_uuid)
            self._make_sub(at, 'date', date_str)
            self._make_sub(at, 'currencyCode', currency)
            self._make_sub(at, 'amount', amount_str)
            self._make_sub(at, 'security', attribs={'reference': sec_ref})

            ce = self._make_sub(at, 'crossEntry', attribs={'class': 'buysell'}, with_id=True)
            self._make_sub(ce, 'portfolio', attribs={'reference': port_el.get('id')})

            pt = self._make_sub(ce, 'portfolioTransaction', with_id=True)
            self._make_sub(pt, 'uuid', port_txn_uuid)
            self._make_sub(pt, 'date', date_str)
            self._make_sub(pt, 'currencyCode', currency)
            self._make_sub(pt, 'amount', amount_str)
            self._make_sub(pt, 'security', attribs={'reference': sec_ref})
            self._make_sub(pt, 'crossEntry', attribs={'class': 'buysell', 'reference': ce.get('id')})
            self._make_sub(pt, 'shares', self._shares_raw(shares))
            self._add_buysell_units(pt, fees, taxes, currency)
            self._make_sub(pt, 'updatedAt', now)
            self._make_sub(pt, 'type', txn_type)

            self._make_sub(ce, 'account', attribs={'reference': acc_el.get('id')})
            self._make_sub(ce, 'accountTransaction', attribs={'reference': at.get('id')})

            self._make_sub(at, 'shares', '0')
            self._make_sub(at, 'updatedAt', now)
            self._make_sub(at, 'type', txn_type)
            if note:
                self._make_sub(at, 'note', note)

            self._make_sub(port_txns, 'portfolio-transaction',
                           attribs={'reference': pt.get('id')})

        self._save(backup=backup)
        log.info("Added %s transaction (portfolio_uuid=%s, account_uuid=%s)",
                 txn_type, port_txn_uuid, acc_txn_uuid)
        return port_txn_uuid

    def _add_delivery(  # pylint: disable=too-many-locals
        self,
        txn_type: str,
        security: str,
        portfolio_id: str,
        date: datetime,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
        backup: bool = True,
    ) -> str:
        """Add a DELIVERY_INBOUND or DELIVERY_OUTBOUND (no cash leg)."""
        self._load()

        sec_el = self._find_security_el(security)
        port_el = self._find_account_el(portfolio_id, 'portfolio')

        txn_uuid = self._gen_uuid()
        now = self._now_iso()

        port_txns = self._get_transactions_el(port_el)

        txn = self._make_sub(port_txns, 'portfolio-transaction', with_id=True)
        self._make_sub(txn, 'uuid', txn_uuid)
        self._make_sub(txn, 'date', self._date_iso(date))
        self._make_sub(txn, 'currencyCode', currency)
        self._make_sub(txn, 'amount', self._amount_raw(amount))
        self._make_sub(txn, 'security', attribs={'reference': sec_el.get('id')})
        self._make_sub(txn, 'shares', self._shares_raw(shares))

        if fees > 0 or taxes > 0:
            units = self._make_sub(txn, 'units')
            if fees > 0:
                self._make_unit(units, 'FEE', fees, currency)
            if taxes > 0:
                self._make_unit(units, 'TAX', taxes, currency)
        else:
            self._make_sub(txn, 'units')

        self._make_sub(txn, 'updatedAt', now)
        self._make_sub(txn, 'type', txn_type)
        if note:
            self._make_sub(txn, 'note', note)

        self._save(backup=backup)
        log.info("Added %s transaction (uuid=%s)", txn_type, txn_uuid)
        return txn_uuid

from datetime import datetime
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from datev.connectors.base import ConnectorError, RemoteParty
from datev.extf import master_data
from datev.intake import ensure_party_account
from datev.parties import assign_number, clear_remote_cache, ensure_in_datev, import_existing, normalize_name
from datev.tests.factories import make_client, make_party_account


def fake_connector(remote=None, next_number=None, can_fetch=True, creates_via_export=False):
	connector = MagicMock()
	connector.can_fetch = can_fetch
	connector.creates_via_export = creates_via_export
	connector.fetch_parties.return_value = remote or []
	connector.next_number.return_value = next_number
	return connector


def use_connector(connector):
	return patch("datev.parties.get_master_data", return_value=connector)


class TestPartyAccounts(IntegrationTestCase):
	def setUp(self):
		clear_remote_cache()
		self.client = make_client()

	def test_ensure_party_account_idempotent(self):
		first = ensure_party_account(
			self.client.name, "Debtor", party_type="User", party="Administrator", party_name="Beispiel GmbH"
		)
		second = ensure_party_account(
			self.client.name,
			"Debtor",
			party_type="User",
			party="Administrator",
			party_name="Beispiel AG",
			vat_id="DE123456789",
			iban="DE02120300000000202051",
			preferred_number=10010,
		)
		self.assertEqual(first, second)
		account = frappe.get_doc("DATEV Party Account", first)
		self.assertEqual(account.sync_status, "New")
		self.assertEqual(
			(account.party_name, account.vat_id, account.iban, account.preferred_number),
			("Beispiel AG", "DE123456789", "DE02120300000000202051", 10010),
		)
		self.assertEqual(frappe.db.count("DATEV Party Account", {"client": self.client.name}), 1)

	def test_source_numbering_uses_preferred(self):
		account = make_party_account(self.client, preferred_number=10020)
		self.assertEqual(assign_number(account), 10020)
		self.assertEqual(account.account_number, 10020)

		account.account_number = 10030
		self.assertEqual(assign_number(account), 10030)

		missing = make_party_account(self.client)
		with self.assertRaises(frappe.ValidationError):
			assign_number(missing)

	def test_datev_numbering_uses_next_available(self):
		self.client.db_set("creditor_numbering", "DATEV next available")
		account = make_party_account(self.client, kind="Creditor", preferred_number=70099)
		connector = fake_connector(next_number=70005)
		with use_connector(connector):
			self.assertEqual(assign_number(account), 70005)
		connector.next_number.assert_called_once_with("Creditor")

	def test_ensure_in_datev_creates_missing_party(self):
		account = make_party_account(self.client, preferred_number=10040, party_name="Neu GmbH")
		connector = fake_connector(remote=[RemoteParty("Debtor", 10041, "Other")])
		with use_connector(connector):
			self.assertTrue(ensure_in_datev(account))
			self.assertTrue(ensure_in_datev(account))
		connector.create_party.assert_called_once()
		account.reload()
		self.assertEqual((account.account_number, account.sync_status), (10040, "Created"))

	def test_ensure_in_datev_via_export_stays_new(self):
		account = make_party_account(self.client, preferred_number=10050)
		connector = fake_connector(can_fetch=False, creates_via_export=True)
		with use_connector(connector):
			self.assertTrue(ensure_in_datev(account))
		connector.create_party.assert_not_called()
		account.reload()
		self.assertEqual((account.account_number, account.sync_status), (10050, "New"))

	def test_number_collision_to_clarify(self):
		remote = [
			RemoteParty("Debtor", 10060, "Ganz Andere KG"),
			RemoteParty("Debtor", 10061, "Muster GmbH"),
		]
		other = make_party_account(self.client, preferred_number=10060, party_name="Neu GmbH")
		same = make_party_account(self.client, preferred_number=10061, party_name="Muster")
		connector = fake_connector(remote=remote)
		with use_connector(connector):
			self.assertFalse(ensure_in_datev(other))
			self.assertTrue(ensure_in_datev(same))
			self.assertFalse(ensure_in_datev(other))
		connector.create_party.assert_not_called()
		connector.fetch_parties.assert_called_once()
		other.reload()
		same.reload()
		self.assertEqual(other.sync_status, "To Clarify")
		self.assertFalse(other.account_number)
		self.assertIn("Ganz Andere KG", other.message)
		self.assertEqual((same.account_number, same.sync_status), (10061, "Imported"))

	def test_ensure_in_datev_error(self):
		self.client.db_set("debtor_numbering", "DATEV next available")
		account = make_party_account(self.client)
		connector = fake_connector()
		connector.next_number.side_effect = frappe.ValidationError("DATEV unreachable")
		with use_connector(connector):
			self.assertFalse(ensure_in_datev(account))
		account.reload()
		self.assertEqual(account.sync_status, "Error")
		self.assertIn("DATEV unreachable", account.message)

	def test_ensure_in_datev_create_error(self):
		account = make_party_account(self.client, preferred_number=10140)
		connector = fake_connector()
		connector.create_party.side_effect = ConnectorError("Rejected by DATEVconnect")
		with use_connector(connector):
			self.assertFalse(ensure_in_datev(account))
		account.reload()
		self.assertEqual((account.account_number, account.sync_status), (10140, "Error"))
		self.assertIn("Rejected by DATEVconnect", account.message)

	def test_settled_status_requires_number(self):
		with self.assertRaises(frappe.ValidationError):
			make_party_account(self.client, sync_status="Imported")

	def test_import_competing_locals_to_clarify(self):
		first = make_party_account(self.client, party_name="Muster GmbH")
		second = make_party_account(self.client, party_name="Muster GmbH")
		result = import_existing(self.client.name, [RemoteParty("Debtor", 10150, "Muster GmbH")])
		self.assertEqual(result, {"imported": 0, "to_clarify": 2, "unmatched": 0})
		for account, competitor in ((first, second), (second, first)):
			account.reload()
			self.assertEqual(account.sync_status, "To Clarify")
			self.assertFalse(account.account_number)
			self.assertIn(competitor.name, account.message)

	def test_import_by_number(self):
		account = make_party_account(self.client, preferred_number=10070, party_name="Lokal")
		result = import_existing(self.client.name, [RemoteParty("Debtor", 10070, "Entfernt")])
		self.assertEqual(result, {"imported": 1, "to_clarify": 0, "unmatched": 0})
		account.reload()
		self.assertEqual((account.account_number, account.sync_status), (10070, "Imported"))

	def test_import_by_vat_id_when_number_matching_off(self):
		self.client.db_set("match_debtors_by_number", 0)
		account = make_party_account(
			self.client, preferred_number=10080, party_name="Lokal", vat_id="de 123 456 789"
		)
		untouched = make_party_account(self.client, preferred_number=10081, party_name="Niemand")
		remote = [
			RemoteParty("Debtor", 10080, "Nummerngleich"),
			RemoteParty("Debtor", 10090, "Steuer", vat_id="DE123456789"),
		]
		result = import_existing(self.client.name, remote)
		self.assertEqual(result, {"imported": 1, "to_clarify": 0, "unmatched": 1})
		account.reload()
		untouched.reload()
		self.assertEqual((account.account_number, account.sync_status), (10090, "Imported"))
		self.assertEqual((untouched.account_number, untouched.sync_status), (0, "New"))

	def test_import_ambiguous_name_to_clarify(self):
		account = make_party_account(self.client, party_name="Muster GmbH")
		remote = [RemoteParty("Debtor", 10100, "Muster AG"), RemoteParty("Debtor", 10101, "MUSTER e.K.")]
		result = import_existing(self.client.name, remote)
		self.assertEqual(result, {"imported": 0, "to_clarify": 1, "unmatched": 0})
		account.reload()
		self.assertEqual(account.sync_status, "To Clarify")
		self.assertFalse(account.account_number)
		self.assertIn("10100", account.message)
		self.assertIn("10101", account.message)

	def test_import_remote_already_linked_to_clarify(self):
		make_party_account(self.client, party_name="Erste", account_number=10110, sync_status="Imported")
		account = make_party_account(self.client, party_name="Zweite", iban="DE02120300000000202051")
		remote = [RemoteParty("Debtor", 10110, "Erste", iban="DE02 1203 0000 0000 2020 51")]
		result = import_existing(self.client.name, remote)
		self.assertEqual(result, {"imported": 0, "to_clarify": 1, "unmatched": 0})
		account.reload()
		self.assertEqual(account.sync_status, "To Clarify")
		self.assertFalse(account.account_number)

	def test_name_normalisation(self):
		self.assertEqual(normalize_name("Muster GmbH & Co. KG"), normalize_name("muster"))
		self.assertEqual(normalize_name("Müller e.K."), "müller")
		self.assertEqual(normalize_name("A.B.C. Handel UG"), "a b c handel")
		account = make_party_account(self.client, kind="Creditor", party_name="muster")
		result = import_existing(self.client.name, [RemoteParty("Creditor", 70010, "Muster GmbH & Co. KG")])
		self.assertEqual(result["imported"], 1)
		account.reload()
		self.assertEqual(account.account_number, 70010)

	def test_import_from_file(self):
		source = make_client()
		exported = [
			frappe._dict(account_number=10120, party_name="Datei GmbH", vat_id=None, iban=None),
			frappe._dict(account_number=70020, party_name="Lieferant AG", vat_id=None, iban=None),
		]
		content = master_data.build(source, exported, datetime(2027, 1, 31, 10, 30))
		file = frappe.get_doc(
			{"doctype": "File", "file_name": "debitoren.csv", "content": content, "is_private": 1}
		).insert()
		debtor = make_party_account(self.client, party_name="Datei")
		creditor = make_party_account(self.client, kind="Creditor", party_name="Lieferant")
		result = self.client.import_parties_from_file(file.file_url)
		self.assertEqual(result, {"imported": 2, "to_clarify": 0, "unmatched": 0})
		debtor.reload()
		creditor.reload()
		self.assertEqual((debtor.account_number, debtor.sync_status), (10120, "Imported"))
		self.assertEqual((creditor.account_number, creditor.sync_status), (70020, "Imported"))

	def test_import_parties_uses_connector(self):
		account = make_party_account(self.client, preferred_number=10130)
		connector = fake_connector(remote=[RemoteParty("Debtor", 10130, "Fern")])
		with use_connector(connector):
			result = self.client.import_parties()
		self.assertEqual(result["imported"], 1)
		account.reload()
		self.assertEqual(account.sync_status, "Imported")

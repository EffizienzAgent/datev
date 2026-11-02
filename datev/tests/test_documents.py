from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from datev import documents, intake
from datev.connectors.base import ConnectorError, StorageConnector, StorageResult
from datev.tests.factories import make_booking, make_client, make_document, make_party_account


class FakeStorage(StorageConnector):
	calls = 0
	outcome: object = StorageResult(external_id="ID-1", program_code="DUO")

	def upload(self, document):
		FakeStorage.calls += 1
		if isinstance(FakeStorage.outcome, Exception):
			raise FakeStorage.outcome
		return FakeStorage.outcome


real_get_hooks = frappe.get_hooks


def fake_hooks(name=None, *args, **kwargs):
	if name == "datev_storage_connectors":
		return [{"Business OS": "datev.tests.test_documents.FakeStorage"}]
	return real_get_hooks(name, *args, **kwargs)


def upsert(client, source_key=None, **overrides):
	source_key = source_key or f"SINV-{frappe.generate_hash(length=8)}"
	values = {
		"direction": "Outgoing",
		"file_name": "invoice.txt",
		"content": b"invoice",
		"document_date": "2027-01-05",
		"document_number": "RE-1",
		"amount": 119,
	}
	values.update(overrides)
	with patch("frappe.enqueue") as enqueue:
		name = intake.upsert_document(client.name, source_key, **values)
	return name, enqueue


def upload_calls(enqueue):
	return [call for call in enqueue.call_args_list if call.args == ("datev.documents.upload",)]


class TestDocuments(IntegrationTestCase):
	def setUp(self):
		frappe.db.delete(
			"DATEV Document", {"source_key": ["in", ["SINV-DISCARD", "SINV-UPLOADED", "SINV-SAME"]]}
		)
		FakeStorage.calls = 0
		FakeStorage.outcome = StorageResult(external_id="ID-1", program_code="DUO")
		for patcher in (patch("frappe.get_hooks", fake_hooks), patch("frappe.db.commit")):
			patcher.start()
			self.addCleanup(patcher.stop)
		self.client_doc = make_client()

	def test_upsert_sets_guid_and_type(self):
		name, enqueue = upsert(self.client_doc)
		doc = frappe.get_doc("DATEV Document", name)
		self.assertTrue(doc.guid)
		self.assertEqual(doc.document_type, "Rechnungsausgang")
		self.assertEqual(doc.status, "Pending")
		file = frappe.get_doc("File", {"file_url": doc.file})
		self.assertEqual(
			(file.is_private, file.attached_to_doctype, file.attached_to_name),
			(1, "DATEV Document", name),
		)
		(call,) = upload_calls(enqueue)
		self.assertEqual(call.kwargs["name"], name)
		self.assertTrue(call.kwargs["enqueue_after_commit"])

	def test_upsert_is_idempotent_after_upload(self):
		name, _enqueue = upsert(self.client_doc, "SINV-SAME")
		documents.upload(name)
		again, enqueue = upsert(self.client_doc, "SINV-SAME", content=b"changed")
		self.assertEqual(again, name)
		self.assertEqual(upload_calls(enqueue), [])
		self.assertEqual(FakeStorage.calls, 1)

	def test_upsert_updates_pending(self):
		name, _enqueue = upsert(self.client_doc, "SINV-SAME")
		guid = frappe.db.get_value("DATEV Document", name, "guid")
		again, enqueue = upsert(self.client_doc, "SINV-SAME", document_number="RE-2")
		self.assertEqual(again, name)
		self.assertEqual(
			frappe.db.get_value("DATEV Document", name, ["guid", "document_number"]), (guid, "RE-2")
		)
		self.assertEqual(len(upload_calls(enqueue)), 1)

	def test_unknown_type_marks_stuck(self):
		client = make_client(document_type_options=[{"document_type": "Something Else"}])
		name, enqueue = upsert(client)
		doc = frappe.get_doc("DATEV Document", name)
		self.assertEqual(doc.status, "Stuck")
		self.assertIn("Rechnungsausgang", doc.message)
		self.assertEqual(upload_calls(enqueue), [])

	def test_upload_success_sets_link(self):
		name, _enqueue = upsert(self.client_doc)
		documents.upload(name)
		doc = frappe.get_doc("DATEV Document", name)
		self.assertEqual((doc.status, doc.external_id, doc.program_code), ("Uploaded", "ID-1", "DUO"))
		self.assertTrue(frappe.db.exists("DATEV Event Log", {"reference_name": name, "event": "uploaded"}))

	def test_upload_result_persisted_and_committed_before_logging(self):
		name, _enqueue = upsert(self.client_doc)
		with (
			patch("datev.documents.log_event", side_effect=RuntimeError("log failed")),
			patch("frappe.db.commit") as commit,
		):
			with self.assertRaises(RuntimeError):
				documents.upload(name)
		commit.assert_called_once()
		self.assertEqual(
			frappe.db.get_value("DATEV Document", name, ["status", "external_id"]), ("Uploaded", "ID-1")
		)

	def test_disabled_client_is_skipped(self):
		self.client_doc.db_set("enabled", 0)
		name, enqueue = upsert(self.client_doc)
		self.assertEqual(upload_calls(enqueue), [])
		documents.upload(name)
		self.assertEqual(
			(FakeStorage.calls, frappe.db.get_value("DATEV Document", name, "status")), (0, "Pending")
		)

	def test_email_upload_sets_no_link(self):
		FakeStorage.outcome = StorageResult(external_id=None, program_code=None)
		name, _enqueue = upsert(self.client_doc)
		documents.upload(name)
		self.assertEqual(frappe.db.get_value("DATEV Document", name, "status"), "No Link")

	def test_upload_skips_non_pending(self):
		name, _enqueue = upsert(self.client_doc)
		documents.upload(name)
		documents.upload(name)
		self.assertEqual(FakeStorage.calls, 1)

	def test_backoff_then_stuck(self):
		FakeStorage.outcome = ConnectorError("down", retryable=True)
		name, _enqueue = upsert(self.client_doc)
		waits = []
		for attempt in range(1, 5):
			documents.upload(name)
			doc = frappe.get_doc("DATEV Document", name)
			self.assertEqual((doc.status, doc.attempts), ("Pending", attempt))
			waits.append((doc.next_attempt - now_datetime()).total_seconds() / 60)
		self.assertEqual(waits, sorted(waits))
		for wait, expected in zip(waits, documents.BACKOFF_MINUTES, strict=True):
			self.assertAlmostEqual(wait, expected, delta=1)
		documents.upload(name)
		doc = frappe.get_doc("DATEV Document", name)
		self.assertEqual((doc.status, doc.attempts, doc.message), ("Stuck", 5, "down"))
		self.assertEqual(
			frappe.db.count("DATEV Event Log", {"reference_name": name, "event": "upload_retry"}), 4
		)
		self.assertTrue(
			frappe.db.exists("DATEV Event Log", {"reference_name": name, "event": "upload_failed"})
		)

	def test_non_retryable_is_stuck_immediately(self):
		FakeStorage.outcome = ConnectorError("bad request")
		name, _enqueue = upsert(self.client_doc)
		documents.upload(name)
		doc = frappe.get_doc("DATEV Document", name)
		self.assertEqual((doc.status, doc.message), ("Stuck", "bad request"))

	def test_retry_due_picks_due_documents(self):
		due, _enqueue = upsert(self.client_doc)
		later, _enqueue = upsert(self.client_doc)
		fresh, _enqueue = upsert(self.client_doc)
		old = add_to_date(now_datetime(), minutes=-20)
		past = add_to_date(now_datetime(), minutes=-1)
		future = add_to_date(now_datetime(), minutes=10)
		frappe.db.set_value("DATEV Document", due, "next_attempt", past)
		frappe.db.set_value("DATEV Document", later, "next_attempt", future)
		old_name, _enqueue = upsert(self.client_doc)
		frappe.db.set_value("DATEV Document", old_name, "creation", old)

		with patch("frappe.db.commit"):
			documents.retry_due()
		uploaded = {
			name: frappe.db.get_value("DATEV Document", name, "status")
			for name in (due, later, fresh, old_name)
		}
		self.assertEqual(
			[uploaded[name] for name in (due, later, fresh, old_name)],
			["Uploaded", "Pending", "Pending", "Uploaded"],
		)

	def test_retry_due_isolates_unexpected_errors(self):
		broken, _enqueue = upsert(self.client_doc)
		healthy, _enqueue = upsert(self.client_doc)
		past = add_to_date(now_datetime(), minutes=-1)
		for name in (broken, healthy):
			frappe.db.set_value("DATEV Document", name, "next_attempt", past)
		original = FakeStorage.upload

		def upload(self, document):
			if document.name == broken:
				raise RuntimeError("boom")
			return original(self, document)

		with (
			patch.object(FakeStorage, "upload", upload),
			patch("frappe.db.commit"),
			patch("frappe.db.rollback"),
		):
			documents.retry_due()
		self.assertEqual(frappe.db.get_value("DATEV Document", healthy, "status"), "Uploaded")
		self.assertEqual(
			frappe.db.get_value("DATEV Document", broken, ["status", "message"]), ("Stuck", "boom")
		)

	def test_discard_before_upload(self):
		name, _enqueue = upsert(self.client_doc, "SINV-DISCARD")
		document = frappe.get_doc("DATEV Document", name)
		booking = make_booking(document, make_party_account(self.client_doc))
		intake.discard_document("SINV-DISCARD")
		self.assertEqual(frappe.db.get_value("DATEV Document", name, "status"), "Discarded")
		self.assertFalse(frappe.db.exists("DATEV Booking", booking.name))
		self.assertTrue(frappe.db.exists("DATEV Event Log", {"reference_name": name, "event": "discarded"}))
		documents.upload(name)
		self.assertEqual(FakeStorage.calls, 0)

	def test_discard_unknown_source_key(self):
		with self.assertRaises(frappe.DoesNotExistError):
			intake.discard_document("SINV-DOES-NOT-EXIST")

	def test_discarded_document_takes_no_bookings(self):
		name, _enqueue = upsert(self.client_doc, "SINV-DISCARD")
		intake.discard_document("SINV-DISCARD")
		intake.discard_document("SINV-DISCARD")
		party = make_party_account(self.client_doc)
		with self.assertRaises(frappe.ValidationError):
			intake.add_booking(
				name,
				"1",
				kind="Outgoing Invoice",
				party_account=party.name,
				contra_account=4400,
				amount=119,
				debit_credit="S",
			)

	def test_discard_after_upload_rejected(self):
		name, _enqueue = upsert(self.client_doc, "SINV-UPLOADED")
		documents.upload(name)
		with self.assertRaises(frappe.ValidationError):
			intake.discard_document("SINV-UPLOADED")
		self.assertEqual(frappe.db.get_value("DATEV Document", name, "status"), "Uploaded")

	def test_retry_resets_stuck(self):
		FakeStorage.outcome = ConnectorError("bad request")
		name, _enqueue = upsert(self.client_doc)
		documents.upload(name)
		FakeStorage.outcome = StorageResult(external_id="ID-2", program_code="DUO")
		documents.retry(name)
		doc = frappe.get_doc("DATEV Document", name)
		self.assertEqual((doc.status, doc.external_id, doc.attempts), ("Uploaded", "ID-2", 0))

	def test_retry_requires_role(self):
		doc = make_document(self.client_doc, status="Stuck")
		with patch("frappe.only_for", side_effect=frappe.PermissionError) as only_for:
			with self.assertRaises(frappe.PermissionError):
				documents.retry(doc.name)
		only_for.assert_called_once_with(["DATEV User", "DATEV Manager"])

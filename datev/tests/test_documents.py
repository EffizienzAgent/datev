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

	def test_document_without_file_not_required(self):
		name, enqueue = upsert(self.client_doc, direction="Other", file_name=None, content=None)
		document = frappe.get_doc("DATEV Document", name)
		self.assertEqual((document.status, document.file), ("Not Required", None))
		self.assertFalse(
			frappe.db.exists("File", {"attached_to_doctype": "DATEV Document", "attached_to_name": name})
		)
		self.assertEqual(upload_calls(enqueue), [])
		documents.upload(name)
		self.assertEqual(FakeStorage.calls, 0)
		self.assertEqual(upsert(self.client_doc, document.source_key, content=b"late")[0], name)
		self.assertEqual(frappe.db.get_value("DATEV Document", name, "status"), "Not Required")

	def test_document_without_file_only_for_other(self):
		for direction in ("Outgoing", "Incoming"):
			with self.subTest(direction=direction), self.assertRaises(frappe.ValidationError):
				upsert(self.client_doc, direction=direction, file_name=None, content=None)
		name, _enqueue = upsert(self.client_doc, "SINV-SAME", direction="Other")
		file = frappe.db.get_value("DATEV Document", name, "file")
		with self.assertRaises(frappe.ValidationError):
			upsert(self.client_doc, "SINV-SAME", direction="Other", file_name=None, content=None)
		self.assertEqual(frappe.db.get_value("DATEV Document", name, ["status", "file"]), ("Pending", file))
		self.assertTrue(frappe.db.exists("File", {"file_url": file}))

	def test_document_without_file_takes_no_invoice_bookings(self):
		name, _enqueue = upsert(self.client_doc, direction="Other", file_name=None, content=None)
		party = make_party_account(self.client_doc)
		values = {"party_account": party.name, "contra_account": 1800, "amount": 10, "debit_credit": "H"}
		with self.assertRaises(frappe.ValidationError):
			intake.add_booking(name, "1", kind="Outgoing Invoice", **values)
		intake.add_booking(name, "1", kind="Payment", **values)

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

	def test_retry_enqueues_and_reresolves(self):
		client = make_client(document_type_options=[{"document_type": "Something Else"}])
		name, _enqueue = upsert(client)
		self.assertEqual(frappe.db.get_value("DATEV Document", name, "status"), "Stuck")
		client.append("document_type_options", {"document_type": "Rechnungsausgang"})
		client.save()
		with patch("frappe.enqueue") as enqueue:
			documents.retry(name)
		doc = frappe.get_doc("DATEV Document", name)
		self.assertEqual(
			(doc.status, doc.document_type, doc.attempts, doc.message), ("Pending", "Rechnungsausgang", 0, "")
		)
		(call,) = upload_calls(enqueue)
		self.assertEqual((call.kwargs["name"], call.kwargs["queue"]), (name, "short"))
		self.assertEqual(FakeStorage.calls, 0)
		documents.upload(name)
		self.assertEqual(frappe.db.get_value("DATEV Document", name, "status"), "Uploaded")

	def test_stuck_never_overwrites_uploaded(self):
		name, _enqueue = upsert(self.client_doc)
		document = frappe.get_doc("DATEV Document", name)
		documents.upload(name)
		documents.mark_stuck(name, "late failure")
		documents.handle_failure(document, ConnectorError("late failure"))
		self.assertEqual(
			frappe.db.get_value("DATEV Document", name, ["status", "external_id", "message"]),
			("Uploaded", "ID-1", ""),
		)
		self.assertFalse(
			frappe.db.exists("DATEV Event Log", {"reference_name": name, "event": "upload_failed"})
		)

	def test_mark_uploaded(self):
		FakeStorage.outcome = ConnectorError("Upload may have reached DATEV.")
		name, _enqueue = upsert(self.client_doc)
		documents.upload(name)
		with self.assertRaises(frappe.ValidationError):
			documents.mark_uploaded(name, "DUO-ID-7", " ")
		documents.mark_uploaded(name, "DUO-ID-7", "Found in DUo")
		doc = frappe.get_doc("DATEV Document", name)
		self.assertEqual(
			(doc.status, doc.external_id, doc.program_code, doc.message), ("Uploaded", "DUO-ID-7", "BEDI", "")
		)
		log = frappe.get_all(
			"DATEV Event Log", filters={"reference_name": name, "event": "marked_uploaded"}, pluck="message"
		)
		self.assertEqual(len(log), 1)
		self.assertIn("Found in DUo", log[0])
		with self.assertRaises(frappe.ValidationError):
			documents.mark_uploaded(name, "DUO-ID-8", "again")
		FakeStorage.outcome = ConnectorError("bad request")
		other, _enqueue = upsert(self.client_doc)
		documents.upload(other)
		with self.assertRaises(frappe.ValidationError):
			documents.mark_uploaded(other, "DUO-ID-7", "same ID")
		with patch("frappe.only_for", side_effect=frappe.PermissionError) as only_for:
			with self.assertRaises(frappe.PermissionError):
				documents.mark_uploaded(name, "DUO-ID-8", "again")
		only_for.assert_called_once_with("DATEV Manager")

	def test_throttle_defers(self):
		first, _enqueue = upsert(self.client_doc)
		second, _enqueue = upsert(self.client_doc)
		other, _enqueue = upsert(make_client())
		with patch.object(FakeStorage, "min_interval", 5):
			documents.upload(first)
			documents.upload(second)
			documents.upload(other)
		self.assertEqual(FakeStorage.calls, 2)
		doc = frappe.get_doc("DATEV Document", second)
		self.assertEqual((doc.status, doc.attempts), ("Pending", 0))
		wait = (doc.next_attempt - now_datetime()).total_seconds()
		self.assertTrue(0 < wait <= 5, wait)
		self.assertEqual(frappe.db.get_value("DATEV Document", other, "status"), "Uploaded")

	def test_retry_requires_role(self):
		doc = make_document(self.client_doc, status="Stuck")
		with patch("frappe.only_for", side_effect=frappe.PermissionError) as only_for:
			with self.assertRaises(frappe.PermissionError):
				documents.retry(doc.name)
		only_for.assert_called_once_with(["DATEV User", "DATEV Manager"])

	def test_drain_uploads_with_spacing(self):
		names = [upsert(self.client_doc)[0] for _ in range(3)]
		later, _enqueue = upsert(self.client_doc)
		frappe.db.set_value("DATEV Document", later, "next_attempt", add_to_date(now_datetime(), hours=1))
		with (
			patch.object(FakeStorage, "min_interval", 5),
			patch("datev.documents.take_slot", return_value=True),
			patch("datev.documents.time.sleep") as sleep,
		):
			documents.drain(self.client_doc.name)
		self.assertEqual(FakeStorage.calls, 3)
		self.assertEqual(
			[frappe.db.get_value("DATEV Document", name, "status") for name in names], ["Uploaded"] * 3
		)
		self.assertEqual(frappe.db.get_value("DATEV Document", later, "status"), "Pending")
		self.assertEqual(sleep.call_count, 2)
		for call in sleep.call_args_list:
			self.assertTrue(4 < call.args[0] <= 5, call.args[0])

	def test_intake_burst_enqueues_one_drain_per_client(self):
		other = make_client()
		with patch.object(FakeStorage, "min_interval", 5), patch("frappe.enqueue") as enqueue:
			for client in (self.client_doc, self.client_doc, self.client_doc, other):
				intake.upsert_document(
					client.name,
					f"SINV-{frappe.generate_hash(length=8)}",
					direction="Outgoing",
					file_name="invoice.txt",
					content=b"invoice",
					document_date="2027-01-05",
					document_number="RE-1",
					amount=119,
				)
		drains = [call for call in enqueue.call_args_list if call.args == ("datev.documents.drain",)]
		self.assertEqual(
			sorted(call.kwargs["client"] for call in drains), sorted([self.client_doc.name, other.name])
		)
		for call in drains:
			self.assertEqual(
				(
					call.kwargs["queue"],
					call.kwargs["job_id"],
					call.kwargs["deduplicate"],
					call.kwargs["timeout"],
				),
				("long", f"datev-upload-{call.kwargs['client']}", True, 3600),
			)
		self.assertEqual(upload_calls(enqueue), [])

	def test_retry_due_enqueues_drain(self):
		name, _enqueue = upsert(self.client_doc)
		frappe.db.set_value("DATEV Document", name, "next_attempt", add_to_date(now_datetime(), minutes=-1))
		with patch.object(FakeStorage, "min_interval", 5), patch("frappe.enqueue") as enqueue:
			documents.retry_due()
		self.assertIn(self.client_doc.name, [call.kwargs.get("client") for call in enqueue.call_args_list])
		self.assertEqual(FakeStorage.calls, 0)

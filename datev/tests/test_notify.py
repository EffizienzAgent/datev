from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from datev import dispatch, notify
from datev.datev.report.datev_status.datev_status import execute
from datev.protect import internal
from datev.tests.factories import make_booking, make_client, make_document, make_party_account

BATCH = "DATEV Batch"


def make_user(role=None):
	email = f"notify-{frappe.generate_hash(length=8)}@example.com"
	user = frappe.get_doc(
		{"doctype": "User", "email": email, "first_name": "Notify", "send_welcome_email": 0}
	)
	if role:
		user.append("roles", {"role": role})
	return user.insert(ignore_permissions=True).name


def make_batch(client, **values):
	values = {"doctype": BATCH, "client": client.name, "status": "Awaiting Approval", **values}
	return internal(frappe.get_doc(values)).insert(ignore_permissions=True)


def logs(user, **filters):
	return frappe.get_all("Notification Log", filters={"for_user": user, **filters}, fields=["*"])


class TestNotify(IntegrationTestCase):
	def setUp(self):
		patcher = patch("frappe.db.commit")
		patcher.start()
		self.addCleanup(patcher.stop)
		self.client_doc = make_client(approval_required=1, dispatch_frequency="Daily", bos_credit_warning=0)
		self.user = make_user()
		self.client_doc.db_set("responsible_user", self.user)

	def test_recipients_fallback_order(self):
		self.assertEqual(notify.recipients(self.client_doc), [self.user])

		role = frappe.get_doc({"doctype": "Role", "role_name": f"Notify {frappe.generate_hash(length=6)}"})
		role.insert()
		role_user = make_user(role.name)
		self.client_doc.db_set({"responsible_user": None, "responsible_role": role.name})
		self.assertEqual(notify.recipients(self.client_doc), [role_user])

		manager = make_user("DATEV Manager")
		self.client_doc.db_set("responsible_role", None)
		self.assertIn(manager, notify.recipients(self.client_doc))
		self.assertNotIn(role_user, notify.recipients(self.client_doc))

	def test_awaiting_approval_notified_once(self):
		batch = make_batch(self.client_doc)
		notify.check()
		notify.check()
		entries = logs(self.user, document_type=BATCH, document_name=batch.name)
		self.assertEqual(len(entries), 1)
		self.assertEqual(entries[0].type, "Alert")
		self.assertTrue(frappe.db.get_value(BATCH, batch.name, "notified_on"))

	def test_sending_batch_notified_after_an_hour(self):
		batch = make_batch(self.client_doc, status="Sending")
		notify.check()
		self.assertEqual(logs(self.user, document_name=batch.name), [])

		frappe.db.set_value(
			BATCH, batch.name, "modified", add_to_date(now_datetime(), hours=-2), update_modified=False
		)
		notify.check()
		notify.check()
		self.assertEqual(len(logs(self.user, document_name=batch.name)), 1)

	def test_overdue_detection(self):
		now = datetime(2027, 3, 10, 12, 0)
		client = frappe.get_doc("DATEV Client", self.client_doc.name)
		document = make_document(client, status="Uploaded", external_id="ID-1", program_code="BEDI")
		make_booking(document, make_party_account(client), status="Stuck")
		self.assertTrue(dispatch.is_overdue(client, now))

		client.last_dispatch_on = datetime(2027, 3, 9, 8, 0)
		self.assertFalse(dispatch.is_overdue(client, now))
		client.last_dispatch_on = datetime(2027, 3, 8, 8, 0)
		self.assertTrue(dispatch.is_overdue(client, now))

		client.dispatch_frequency, client.dispatch_weekday = "Weekly", "Friday"
		client.last_dispatch_on = datetime(2027, 3, 5, 8, 0)
		self.assertFalse(dispatch.is_overdue(client, now))
		client.last_dispatch_on = datetime(2027, 2, 26, 8, 0)
		self.assertTrue(dispatch.is_overdue(client, now))

		client.dispatch_frequency, client.dispatch_day_of_month = "Monthly", 5
		client.last_dispatch_on = datetime(2027, 3, 5, 8, 0)
		self.assertFalse(dispatch.is_overdue(client, now))
		client.last_dispatch_on = datetime(2027, 2, 5, 8, 0)
		self.assertTrue(dispatch.is_overdue(client, now))

		frappe.db.set_value("DATEV Booking", {"client": client.name}, "status", "Failed")
		self.assertTrue(dispatch.is_overdue(client, now))
		frappe.db.set_value("DATEV Booking", {"client": client.name}, "status", "Transferred")
		self.assertFalse(dispatch.is_overdue(client, now))

	def test_overdue_by_old_approval_batch(self):
		now = now_datetime()
		client = frappe.get_doc("DATEV Client", self.client_doc.name)
		client.last_dispatch_on = add_to_date(now, days=-9)
		batch = make_batch(client)
		self.assertFalse(dispatch.is_overdue(client, now))
		frappe.db.set_value(BATCH, batch.name, "creation", add_to_date(now, days=-2))
		self.assertTrue(dispatch.is_overdue(client, now))

	def test_overdue_notified_once_a_day(self):
		client = frappe.get_doc("DATEV Client", self.client_doc.name)
		document = make_document(client, status="Uploaded", external_id="ID-1", program_code="BEDI")
		make_booking(document, make_party_account(client), status="Pending")
		client.db_set("last_dispatch_on", add_to_date(now_datetime(), days=-5))
		notify.check()
		notify.check()
		entries = logs(self.user, subject=("like", "%overdue%"))
		self.assertEqual(len(entries), 1)

	def test_stuck_summary_once_a_day(self):
		client = frappe.get_doc("DATEV Client", self.client_doc.name)
		make_document(client, status="Stuck", message="boom")
		make_party_account(client, sync_status="To Clarify")
		notify.check()
		notify.check()
		entries = logs(self.user, subject=("like", "%stuck%"))
		self.assertEqual(len(entries), 1)
		self.assertIn("1 stuck documents", entries[0].subject)

	def test_stuck_summary_counts_failed_bookings_and_party_errors(self):
		client = frappe.get_doc("DATEV Client", self.client_doc.name)
		document = make_document(client, status="Uploaded", external_id="ID-1")
		make_booking(document, make_party_account(client, sync_status="Error"), status="Failed")
		notify.check()
		entries = logs(self.user, subject=("like", "%stuck%"))
		self.assertEqual(len(entries), 1)
		self.assertIn("1 failed bookings", entries[0].subject)
		self.assertIn("1 party accounts with errors", entries[0].subject)

	def test_credit_warning(self):
		client = frappe.get_doc("DATEV Client", self.client_doc.name)
		client.db_set({"bos_credits": 3, "bos_credit_warning": 10})
		notify.check()
		self.assertEqual(len(logs(self.user)), 0)

		client.db_set("bos_credits_updated_on", now_datetime())
		notify.check()
		notify.check()
		self.assertEqual(len(logs(self.user)), 1)

		other = make_client(storage_connector="E-mail to DUo", duo_email="duo@example.com")
		other.db_set(
			{
				"bos_credits": 3,
				"bos_credits_updated_on": now_datetime(),
				"bos_credit_warning": 10,
				"responsible_user": self.user,
			}
		)
		notify.check()
		self.assertEqual(len(logs(self.user)), 1)

		client.db_set("bos_credits", 50)
		frappe.cache.delete_value(notify.cache_key(client.name, "credits"))
		notify.check()
		self.assertEqual(len(logs(self.user)), 1)

	def test_report_flags_problems_first(self):
		quiet = make_client(bos_credit_warning=0)
		problem = frappe.get_doc("DATEV Client", self.client_doc.name)
		make_document(problem, status="Stuck")
		make_document(problem, status="No Link")
		make_batch(problem)
		make_batch(problem, status="Sending")
		make_party_account(problem, sync_status="To Clarify")
		make_party_account(problem, sync_status="Error")
		make_booking(make_document(problem), make_party_account(problem), status="Failed")
		problem.db_set("bos_credits", 7)

		columns, rows = execute({})
		fields = [column["fieldname"] for column in columns]
		self.assertEqual(
			fields,
			[
				"client",
				"transferred_30d",
				"awaiting_approval",
				"sending_batches",
				"stuck_bookings",
				"failed_bookings",
				"stuck_documents",
				"documents_without_link",
				"held",
				"parties_to_clarify",
				"party_errors",
				"overdue",
				"bos_credits",
			],
		)
		by_client = {row["client"]: row for row in rows}
		row = by_client[problem.name]
		self.assertEqual(
			(
				row["awaiting_approval"],
				row["sending_batches"],
				row["stuck_documents"],
				row["documents_without_link"],
				row["parties_to_clarify"],
				row["party_errors"],
				row["failed_bookings"],
				row["bos_credits"],
			),
			(1, 1, 1, 1, 1, 1, 1, 7),
		)
		self.assertLess(list(by_client).index(problem.name), list(by_client).index(quiet.name))
		self.assertEqual(by_client[quiet.name]["stuck_documents"], 0)

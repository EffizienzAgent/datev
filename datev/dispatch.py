from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, getdate, now_datetime

from datev.connectors import registry
from datev.connectors.base import ConnectorError
from datev.connectors.datevconnect import clear_fiscal_year_cache
from datev.events import log_event
from datev.intake import BOOKING, OPEN_BOOKING_STATUSES
from datev.parties import ensure_in_datev, set_status
from datev.protect import internal

CLIENT = "DATEV Client"
BATCH = "DATEV Batch"
DOCUMENT = "DATEV Document"
PARTY = "DATEV Party Account"
DISPATCH_ROLES = ["DATEV User", "DATEV Manager"]
LINKED_STATUSES = ("Uploaded", "No Link")
MANDATORY_FIELDS = ("party_account", "contra_account", "document_field_1", "document_date")
RESETTABLE_STATUSES = ("Transferred", "Exported", "Failed")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def preflight(booking) -> str | None:
	missing = [_(booking.meta.get_label(field)) for field in MANDATORY_FIELDS if not booking.get(field)]
	if missing:
		return _("Missing fields: {0}").format(", ".join(missing))

	client = frappe.get_doc(CLIENT, booking.client)
	message = registry.get_export(client).check_booking(booking)
	if message:
		return message

	document = frappe.get_doc(DOCUMENT, booking.document)
	if document.status not in LINKED_STATUSES:
		return _("Document {0} is not in DATEV yet (status {1}).").format(document.name, _(document.status))
	if client.require_document_link and not (document.status == "Uploaded" and document.external_id):
		return _("Document {0} has no document link, which this client requires.").format(document.name)

	party_account = frappe.get_doc(PARTY, booking.party_account)
	if not ensure_in_datev(party_account):
		return _("Party account {0} is not ready: {1}").format(
			party_account.name, party_account.message or _(party_account.sync_status)
		)
	return None


def is_due(client, now) -> bool:
	last = client.last_dispatch_on and getdate(client.last_dispatch_on)
	today = now.date()
	if client.dispatch_frequency == "Monthly":
		if now.day < (client.dispatch_day_of_month or 1):
			return False
		return not last or (last.year, last.month) != (today.year, today.month)
	if client.dispatch_frequency == "Weekly" and WEEKDAYS[today.weekday()] != client.dispatch_weekday:
		return False
	return not last or last < today


def previous_due_date(client, today):
	for days in range(1, 63):
		day = today - timedelta(days=days)
		if is_due(frappe._dict(client.as_dict(), last_dispatch_on=None), datetime.combine(day, time.max)):
			return day
	return None


def is_overdue(client, now) -> bool:
	due_date = previous_due_date(client, now.date())
	if not due_date or not is_due(client, datetime.combine(due_date, time.max)):
		return False
	if frappe.db.exists(BOOKING, {"client": client.name, "status": ("in", OPEN_BOOKING_STATUSES), "held": 0}):
		return True
	return bool(
		frappe.db.exists(
			BATCH,
			{
				"client": client.name,
				"status": "Awaiting Approval",
				"creation": ("<", add_to_date(now, hours=-24)),
			},
		)
	)


def collect(client: str) -> str | None:
	frappe.db.get_value(CLIENT, client, "name", for_update=True)
	client_doc = frappe.get_doc(CLIENT, client)
	if not client_doc.enabled:
		return None
	clear_fiscal_year_cache()
	ready = []
	for name in frappe.get_all(
		BOOKING,
		filters={"client": client, "status": ("in", OPEN_BOOKING_STATUSES), "held": 0},
		pluck="name",
		order_by="creation asc",
	):
		booking = frappe.get_doc(BOOKING, name, for_update=True)
		if booking.status not in OPEN_BOOKING_STATUSES or booking.held:
			continue
		message = preflight(booking)
		if message:
			booking.db_set({"status": "Stuck", "message": message})
		else:
			ready.append(booking)
	ready = still_open(client, ready)
	if not ready:
		return None

	batch = internal(
		frappe.get_doc(
			{
				"doctype": BATCH,
				"client": client,
				"export_connector": client_doc.export_connector,
				"status": "Awaiting Approval" if client_doc.approval_required else "Approved",
			}
		)
	).insert(ignore_permissions=True)
	for booking in ready:
		booking.db_set({"status": "Batched", "batch": batch.name, "message": ""})
	client_doc.db_set("last_dispatch_on", now_datetime())
	return batch.name


def still_open(client: str, bookings: list) -> list:
	frappe.db.get_value(CLIENT, client, "name", for_update=True)
	result = []
	for booking in bookings:
		current = frappe.db.get_value(BOOKING, booking.name, ["status", "batch", "held"], for_update=True)
		if current and current[0] in OPEN_BOOKING_STATUSES and not current[1] and not current[2]:
			result.append(booking)
	return result


def enqueue_send(batch: str) -> None:
	frappe.enqueue("datev.dispatch.send", queue="long", enqueue_after_commit=True, batch=batch)


@frappe.whitelist()
def approve(batch: str) -> None:
	client = frappe.db.get_value(BATCH, batch, "client")
	if not client:
		return
	check_approver(frappe.get_doc(CLIENT, client))
	if frappe.db.get_value(BATCH, batch, "status", for_update=True) != "Awaiting Approval":
		return
	frappe.db.set_value(
		BATCH,
		batch,
		{"status": "Approved", "approved_by": frappe.session.user, "approved_on": now_datetime()},
	)
	log_event(BATCH, batch, "approved")
	enqueue_send(batch)


def check_approver(client) -> None:
	roles = set(frappe.get_roles())
	if roles & set(DISPATCH_ROLES):
		return
	if client.responsible_user and client.responsible_user == frappe.session.user:
		return
	if client.responsible_role and client.responsible_role in roles:
		return
	frappe.throw(_("You are not allowed to approve batches of this client."), frappe.PermissionError)


@frappe.whitelist()
def hold(booking: str, held: bool = True) -> None:
	frappe.only_for(DISPATCH_ROLES)
	held = frappe.utils.sbool(held)
	doc = frappe.get_doc(BOOKING, booking, for_update=True)
	if not held and doc.status in OPEN_BOOKING_STATUSES:
		doc.db_set("held", 0)
		return
	if held and doc.status == "Pending":
		doc.db_set("held", 1)
		return
	batch = doc.batch
	if not held or doc.status != "Batched" or not batch:
		frappe.throw(_("Booking {0} cannot be held.").format(booking))
	if frappe.db.get_value(BATCH, batch, "status", for_update=True) != "Awaiting Approval":
		frappe.throw(_("Booking {0} is in a batch that is no longer awaiting approval.").format(booking))
	doc.db_set({"status": "Pending", "held": 1, "batch": None})
	if not frappe.db.exists(BOOKING, {"batch": batch}):
		frappe.delete_doc(BATCH, batch, ignore_permissions=True)


def send(batch: str) -> None:
	if frappe.db.get_value(BATCH, batch, "status", for_update=True) != "Approved":
		return
	bookings = batched(batch)
	if not bookings:
		message = _("No bookings left to send.")
		frappe.db.set_value(BATCH, batch, {"status": "Sent", "message": message, "sent_on": now_datetime()})
		log_event(BATCH, batch, "sent", message)
		return
	frappe.db.set_value(BATCH, batch, "status", "Sending")
	frappe.db.commit()

	batch_doc = frappe.get_doc(BATCH, batch)
	connector = None
	try:
		connector = registry.get_export(frappe.get_doc(CLIENT, batch_doc.client), batch_doc.export_connector)
		result = connector.export(batch_doc, bookings)
	except Exception as error:
		frappe.db.rollback()
		if connector is None or not connector.remote or getattr(error, "nothing_sent", False):
			reset(batch, bookings, error)
		else:
			keep_uncertain(batch, error)
	else:
		record(batch, bookings, connector.final_status, result)
	frappe.db.commit()


def batched(batch: str) -> list:
	return [
		frappe.get_doc(BOOKING, name)
		for name in frappe.get_all(
			BOOKING, filters={"batch": batch, "status": "Batched"}, pluck="name", order_by="creation asc"
		)
	]


def reset(batch: str, bookings: list, error: Exception) -> None:
	if not isinstance(error, ConnectorError):
		frappe.log_error(title="DATEV export failed", reference_doctype=BATCH, reference_name=batch)
	for booking in bookings:
		booking.db_set({"status": "Pending", "batch": None, "message": str(error)})
	frappe.db.set_value(BATCH, batch, {"status": "Failed", "message": str(error), "sent_on": now_datetime()})
	log_event(BATCH, batch, "failed", str(error), getattr(error, "response", None))


def keep_uncertain(batch: str, error: Exception) -> None:
	if not isinstance(error, ConnectorError):
		frappe.log_error(title="DATEV export failed", reference_doctype=BATCH, reference_name=batch)
	message = _("Delivery to DATEV is uncertain. Check DATEV, then resolve the batch.")
	frappe.db.set_value(BATCH, batch, "message", f"{message}\n{error}")
	log_event(BATCH, batch, "delivery_uncertain", str(error), getattr(error, "response", None))


def record(batch: str, bookings: list, final_status: str, result) -> None:
	items = {item.booking: item for item in result.items}
	uncertain = set(result.uncertain)
	sent = []
	for booking in bookings:
		if booking.name in uncertain:
			continue
		item = items.get(booking.name)
		if item and item.ok:
			booking.db_set({"status": final_status, "message": ""})
			sent.append(booking)
		else:
			message = item.message if item else _("The export connector returned no result.")
			booking.db_set({"status": "Failed", "batch": None, "message": message})

	failed = len(bookings) - len(sent) - len(uncertain)
	message = _("{0} sent, {1} failed.").format(len(sent), failed)
	if uncertain:
		status, event = "Sending", "delivery_uncertain"
		message = _("Delivery to DATEV is uncertain. Check DATEV, then resolve the batch.")
		values = {"message": message}
	else:
		status = "Sent" if not failed else "Partially Sent" if sent else "Failed"
		event = frappe.scrub(status)
		values = {"status": status, "message": message, "sent_on": now_datetime()}
	frappe.db.set_value(BATCH, batch, values)
	frappe.db.commit()

	mark_parties_created(sent, final_status)
	log_event(BATCH, batch, event, message, result.response)


def mark_parties_created(bookings: list, final_status: str) -> None:
	if final_status != "Exported":
		return
	for name in {booking.party_account for booking in bookings}:
		party_account = frappe.get_doc(PARTY, name)
		if party_account.sync_status == "New":
			set_status(party_account, "Created", "", "created")


@frappe.whitelist()
def resolve_batch(batch: str, delivered: bool) -> None:
	frappe.only_for("DATEV Manager")
	delivered = frappe.utils.sbool(delivered)
	if frappe.db.get_value(BATCH, batch, "status", for_update=True) != "Sending":
		frappe.throw(_("Only batches that are still sending can be resolved."))
	batch_doc = frappe.get_doc(BATCH, batch)
	final_status = registry.get_export(
		frappe.get_doc(CLIENT, batch_doc.client), batch_doc.export_connector
	).final_status
	bookings = batched(batch)
	if delivered:
		for booking in bookings:
			booking.db_set({"status": final_status, "message": ""})
		mark_parties_created(bookings, final_status)
		status, event = "Sent", "resolved_delivered"
	else:
		for booking in bookings:
			booking.db_set({"status": "Pending", "batch": None, "message": ""})
		partly = frappe.db.exists(BOOKING, {"batch": batch, "status": final_status})
		status, event = ("Partially Sent" if partly else "Failed"), "resolved_not_delivered"
	frappe.db.set_value(BATCH, batch, {"status": status, "message": "", "sent_on": now_datetime()})
	log_event(BATCH, batch, event)


@frappe.whitelist()
def reset_booking(booking: str, reason: str) -> None:
	frappe.only_for("DATEV Manager")
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("A reason is required to reset a booking."))
	doc = frappe.get_doc(BOOKING, booking, for_update=True)
	if doc.status not in RESETTABLE_STATUSES:
		frappe.throw(_("Only transferred, exported or failed bookings can be reset."))
	previous = doc.status
	doc.db_set({"status": "Pending", "batch": None, "message": reason})
	log_event(BOOKING, booking, "reset", _("Reset from {0}: {1}").format(_(previous), reason))


def run() -> None:
	now = now_datetime()
	for client in frappe.get_all(CLIENT, filters={"enabled": 1}, pluck="name"):
		try:
			if is_due(frappe.get_doc(CLIENT, client), now):
				collect(client)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="DATEV booking collection failed", reference_doctype=CLIENT, reference_name=client
			)

	for batch in frappe.get_all(BATCH, filters={"status": "Approved"}, pluck="name", order_by="creation asc"):
		try:
			send(batch)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="DATEV batch dispatch failed", reference_doctype=BATCH, reference_name=batch
			)


@frappe.whitelist()
def send_now(client: str) -> str | None:
	frappe.only_for(DISPATCH_ROLES)
	batch = collect(client)
	if batch:
		enqueue_send(batch)
	return batch

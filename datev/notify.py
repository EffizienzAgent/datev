import frappe
from frappe import _
from frappe.utils import add_to_date, escape_html, now_datetime, today

from datev import dispatch
from datev.events import log_event

CLIENT = "DATEV Client"
BATCH = "DATEV Batch"
BOOKING = "DATEV Booking"
DOCUMENT = "DATEV Document"
PARTY = "DATEV Party Account"
MANAGER_ROLE = "DATEV Manager"
REPORT_LINK = "/app/query-report/DATEV Status"
SENDING_GRACE_HOURS = 1
UNCONFIRMED_GRACE_DAYS = 7
EXTF_EXPORTS = ("EXTF download", "EXTF e-mail")
SENT_STATUSES = ("Sent", "Partially Sent")
STUCK_FIELDS = (
	"stuck_documents",
	"stuck_bookings",
	"failed_bookings",
	"parties_to_clarify",
	"party_errors",
	"unconfirmed_overdue",
)


def enabled_users(**filters) -> list[str]:
	users = frappe.get_all("Has Role", filters={"parenttype": "User", **filters}, pluck="parent")
	return sorted(
		{
			user
			for user in users
			if user not in ("Administrator", "Guest") and frappe.db.get_value("User", user, "enabled")
		}
	)


def recipients(client) -> list[str]:
	if client.responsible_user:
		return [client.responsible_user]
	if client.responsible_role:
		users = enabled_users(role=client.responsible_role)
		if users:
			return users
	return enabled_users(role=MANAGER_ROLE)


def count(doctype: str, **filters) -> int:
	return frappe.db.count(doctype, filters)


def status_counts(client: str, now) -> dict:
	unconfirmed = {
		"client": client,
		"status": ("in", SENT_STATUSES),
		"export_connector": ("in", EXTF_EXPORTS),
		"confirmed_on": ("is", "not set"),
	}
	return {
		"transferred_30d": count(
			BOOKING,
			client=client,
			status=("in", ("Transferred", "Exported")),
			modified=(">=", add_to_date(now, days=-30)),
		),
		"awaiting_approval": count(BATCH, client=client, status="Awaiting Approval"),
		"sending_batches": count(BATCH, client=client, status="Sending"),
		"failed_batches": count(
			BATCH,
			client=client,
			status=("in", ("Failed", "Partially Sent")),
			sent_on=(">=", add_to_date(now, days=-30)),
		),
		"unconfirmed_batches": count(BATCH, **unconfirmed),
		"unconfirmed_overdue": count(
			BATCH, **unconfirmed, sent_on=("<", add_to_date(now, days=-UNCONFIRMED_GRACE_DAYS))
		),
		"stuck_bookings": count(BOOKING, client=client, status="Stuck"),
		"failed_bookings": count(BOOKING, client=client, status="Failed"),
		"stuck_documents": count(DOCUMENT, client=client, status="Stuck"),
		"documents_without_link": count(DOCUMENT, client=client, status="No Link"),
		"held": count(BOOKING, client=client, held=1),
		"parties_to_clarify": count(PARTY, client=client, sync_status="To Clarify"),
		"party_errors": count(PARTY, client=client, sync_status="Error"),
	}


def low_credits(client) -> bool:
	return bool(
		client.storage_connector == "Business OS"
		and client.bos_credits_updated_on
		and client.bos_credit_warning
		and (client.bos_credits or 0) < client.bos_credit_warning
	)


def cache_key(client: str, reason: str) -> str:
	return f"datev:notify:{client}:{reason}:{today()}"


def once_a_day(client: str, reason: str) -> bool:
	key = cache_key(client, reason)
	if frappe.cache.get_value(key):
		return False
	frappe.cache.set_value(key, 1, expires_in_sec=86400)
	return True


def alert(users: list[str], subject: str, **target) -> None:
	for user in users:
		frappe.get_doc(
			{"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": subject, **target}
		).insert(ignore_permissions=True)


def mark_notified(batch: str, now) -> None:
	frappe.db.set_value(BATCH, batch, "notified_on", now, update_modified=False)


def notify_batches(client, users: list[str], now) -> None:
	name = escape_html(client.name)
	awaiting = frappe.get_all(
		BATCH, filters={"client": client.name, "status": "Awaiting Approval"}, pluck="name"
	)
	for batch in awaiting:
		if frappe.db.get_value(BATCH, batch, "notified_on"):
			continue
		subject = _("Batch {0} of {1} is waiting for approval").format(batch, name)
		alert(users, subject, document_type=BATCH, document_name=batch)
		mark_notified(batch, now)

	sending = frappe.get_all(
		BATCH,
		filters={
			"client": client.name,
			"status": "Sending",
			"modified": ("<", add_to_date(now, hours=-SENDING_GRACE_HOURS)),
		},
		fields=["name", "modified", "notified_on"],
	)
	for batch in sending:
		if batch.notified_on and batch.notified_on >= batch.modified:
			continue
		subject = _("Batch {0} of {1} has been sending for over an hour. Check DATEV and resolve it.").format(
			batch.name, name
		)
		alert(users, subject, document_type=BATCH, document_name=batch.name)
		mark_notified(batch.name, now)


def notify_client(client, now) -> None:
	users = recipients(client)
	if not users:
		return
	name = escape_html(client.name)
	link = f"{REPORT_LINK}?client={client.name}"
	notify_batches(client, users, now)

	if dispatch.is_overdue(client, now) and once_a_day(client.name, "overdue"):
		alert(users, _("Dispatch for {0} is overdue").format(name), link=link)

	counts = status_counts(client.name, now)
	if any(counts[field] for field in STUCK_FIELDS) and once_a_day(client.name, "stuck"):
		subject = _(
			"{0}: {1} stuck documents, {2} stuck bookings, {3} failed bookings, "
			"{4} party accounts to clarify, {5} party accounts with errors, "
			"{6} EXTF files not confirmed as imported"
		).format(name, *(counts[field] for field in STUCK_FIELDS))
		alert(users, subject, link=link)

	if low_credits(client) and once_a_day(client.name, "credits"):
		subject = _("{0}: only {1} Business OS credits left (warning at {2})").format(
			name, client.bos_credits, client.bos_credit_warning
		)
		alert(users, subject, link=link)


def file_ready(batch) -> None:
	if frappe.db.exists(
		"DATEV Event Log", {"reference_doctype": BATCH, "reference_name": batch.name, "event": "file_ready"}
	):
		return
	users = recipients(frappe.get_doc(CLIENT, batch.client))
	alert(users, _("DATEV file ready: {0}").format(batch.name), document_type=BATCH, document_name=batch.name)
	log_event(BATCH, batch.name, "file_ready")


def check() -> None:
	now = now_datetime()
	for name in frappe.get_all(CLIENT, filters={"enabled": 1}, pluck="name"):
		try:
			notify_client(frappe.get_doc(CLIENT, name), now)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="DATEV notification failed", reference_doctype=CLIENT, reference_name=name)

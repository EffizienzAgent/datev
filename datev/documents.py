import frappe
from frappe.utils import add_to_date, now_datetime

from datev.connectors import registry
from datev.connectors.base import ConnectorError
from datev.events import log_event
from datev.protect import internal

BACKOFF_MINUTES = (5, 30, 120, 720)
MAX_ATTEMPTS = 5
UNSCHEDULED_GRACE_MINUTES = 10
DOCUMENT = "DATEV Document"


def upload(name: str) -> None:
	status = frappe.db.get_value(DOCUMENT, name, "status", for_update=True)
	if status != "Pending":
		return
	document = frappe.get_doc(DOCUMENT, name)
	client = frappe.get_doc("DATEV Client", document.client)
	if not client.enabled:
		return
	try:
		result = registry.get_storage(client).upload(document)
	except ConnectorError as error:
		handle_failure(document, error)
		return
	document.db_set(
		{
			"external_id": result.external_id,
			"program_code": result.program_code,
			"status": "Uploaded" if result.external_id else "No Link",
			"message": "",
			"next_attempt": None,
		}
	)
	# The document is in DATEV now: commit so a later error cannot roll the link back.
	frappe.db.commit()
	log_event(DOCUMENT, name, "uploaded", response=result.response)


def handle_failure(document, error: ConnectorError) -> None:
	document.attempts = (document.attempts or 0) + 1
	document.message = str(error)
	if error.retryable and document.attempts < MAX_ATTEMPTS:
		document.next_attempt = add_to_date(now_datetime(), minutes=BACKOFF_MINUTES[document.attempts - 1])
		event = "upload_retry"
	else:
		document.status = "Stuck"
		document.next_attempt = None
		event = "upload_failed"
	internal(document).save(ignore_permissions=True)
	log_event(DOCUMENT, document.name, event, str(error), error.response)


def retry_due() -> None:
	now = now_datetime()
	grace = add_to_date(now, minutes=-UNSCHEDULED_GRACE_MINUTES)
	due = frappe.get_all(
		DOCUMENT,
		filters=[
			["status", "=", "Pending"],
			["next_attempt", "is", "set"],
			["next_attempt", "<=", now],
		],
		pluck="name",
	) + frappe.get_all(
		DOCUMENT,
		filters={"status": "Pending", "next_attempt": ["is", "not set"], "creation": ["<=", grace]},
		pluck="name",
	)
	for name in due:
		try:
			upload(name)
		except Exception as error:
			frappe.db.rollback()
			frappe.log_error(
				title="DATEV document upload failed", reference_doctype=DOCUMENT, reference_name=name
			)
			mark_stuck(name, str(error))
		frappe.db.commit()


def mark_stuck(name: str, message: str) -> None:
	frappe.db.set_value(DOCUMENT, name, {"status": "Stuck", "next_attempt": None, "message": message})
	log_event(DOCUMENT, name, "upload_failed", message)


@frappe.whitelist()
def retry(name: str) -> None:
	frappe.only_for(["DATEV User", "DATEV Manager"])
	if frappe.db.get_value(DOCUMENT, name, "status") != "Stuck":
		return
	frappe.db.set_value(
		DOCUMENT, name, {"status": "Pending", "attempts": 0, "next_attempt": None, "message": ""}
	)
	upload(name)

import time

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from datev.connectors import registry
from datev.connectors.base import ConnectorError
from datev.connectors.business_os import PROGRAM_CODE
from datev.document_types import resolve
from datev.events import log_event
from datev.protect import internal

BACKOFF_MINUTES = (5, 30, 120, 720)
MAX_ATTEMPTS = 5
UNSCHEDULED_GRACE_MINUTES = 10
DRAIN_SECONDS = 50 * 60
DRAIN_TIMEOUT = 3600
DOCUMENT = "DATEV Document"


def enqueue_upload(name: str, client) -> None:
	if not registry.get_storage(client).min_interval:
		frappe.enqueue("datev.documents.upload", name=name, enqueue_after_commit=True, queue="short")
		return
	scheduled = frappe.flags.setdefault("datev_drains", set())
	if client.name not in scheduled:
		scheduled.add(client.name)
		enqueue_drain(client.name, after_commit=True)


def enqueue_drain(client: str, after_commit: bool = False) -> None:
	extra = {"enqueue_after_commit": True} if after_commit else {}
	frappe.enqueue(
		"datev.documents.drain",
		queue="long",
		timeout=DRAIN_TIMEOUT,
		job_id=f"datev-upload-{client}",
		deduplicate=True,
		**extra,
		client=client,
	)


def due_filters(now) -> list:
	grace = add_to_date(now, minutes=-UNSCHEDULED_GRACE_MINUTES)
	return [
		[["status", "=", "Pending"], ["next_attempt", "is", "set"], ["next_attempt", "<=", now]],
		{"status": "Pending", "next_attempt": ["is", "not set"], "creation": ["<=", grace]},
	]


def drain(client: str) -> None:
	client_doc = frappe.get_doc("DATEV Client", client)
	if not client_doc.enabled:
		return
	interval = registry.get_storage(client_doc).min_interval
	started = time.monotonic()
	last = None
	tried = set()
	while time.monotonic() - started < DRAIN_SECONDS:
		name = next_due(client, tried)
		if not name:
			return
		tried.add(name)
		if last is not None:
			wait = interval - (time.monotonic() - last)
			if wait > 0:
				time.sleep(wait)
		upload_isolated(name)
		last = time.monotonic()


def upload(name: str) -> None:
	status = frappe.db.get_value(DOCUMENT, name, "status", for_update=True)
	if status != "Pending":
		return
	document = frappe.get_doc(DOCUMENT, name)
	client = frappe.get_doc("DATEV Client", document.client)
	if not client.enabled:
		return
	connector = registry.get_storage(client)
	if not take_slot(client.name, connector.min_interval):
		document.db_set("next_attempt", add_to_date(now_datetime(), seconds=connector.min_interval))
		return
	try:
		result = connector.upload(document)
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


def take_slot(client: str, interval: int) -> bool:
	if not interval:
		return True
	key = frappe.cache.make_key(f"datev:upload-slot:{client}")
	return bool(frappe.cache.set(key, 1, ex=interval, nx=True))


def update_pending(name: str, values: dict) -> bool:
	if frappe.db.get_value(DOCUMENT, name, "status", for_update=True) != "Pending":
		return False
	frappe.db.set_value(DOCUMENT, name, values)
	return True


def handle_failure(document, error: ConnectorError) -> None:
	attempts = (document.attempts or 0) + 1
	values = {"attempts": attempts, "message": str(error)}
	if error.retryable and attempts < MAX_ATTEMPTS:
		values["next_attempt"] = add_to_date(now_datetime(), minutes=BACKOFF_MINUTES[attempts - 1])
		event = "upload_retry"
	else:
		values.update({"status": "Stuck", "next_attempt": None})
		event = "upload_failed"
	if update_pending(document.name, values):
		log_event(DOCUMENT, document.name, event, str(error), error.response)


def next_due(client: str, tried: set) -> str | None:
	now = now_datetime()
	for row in frappe.get_all(
		DOCUMENT,
		filters={"client": client, "status": "Pending"},
		fields=["name", "next_attempt"],
		order_by="creation asc",
	):
		if row.name not in tried and (not row.next_attempt or row.next_attempt <= now):
			return row.name
	return None


def retry_due() -> None:
	by_client = {}
	for filters in due_filters(now_datetime()):
		for row in frappe.get_all(
			DOCUMENT, filters=filters, fields=["name", "client"], order_by="creation asc"
		):
			by_client.setdefault(row.client, []).append(row.name)
	for client, names in by_client.items():
		try:
			throttled = registry.get_storage(frappe.get_doc("DATEV Client", client)).min_interval
		except Exception:
			frappe.log_error(
				title="DATEV document upload failed", reference_doctype="DATEV Client", reference_name=client
			)
			continue
		if throttled:
			enqueue_drain(client)
			continue
		for name in names:
			upload_isolated(name)


def upload_isolated(name: str) -> None:
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
	if update_pending(name, {"status": "Stuck", "next_attempt": None, "message": message}):
		log_event(DOCUMENT, name, "upload_failed", message)


@frappe.whitelist(methods=["POST"])
def retry(name: str) -> None:
	frappe.only_for(["DATEV User", "DATEV Manager"])
	document = frappe.get_doc(DOCUMENT, name, for_update=True)
	if document.status != "Stuck":
		return
	client = frappe.get_doc("DATEV Client", document.client)
	document.update(
		{
			"document_type": resolve(client, document.direction, document.source_doctype, document.is_return),
			"status": "Pending",
			"attempts": 0,
			"next_attempt": None,
			"message": "",
		}
	)
	internal(document).save(ignore_permissions=True)
	log_event(DOCUMENT, name, "retried")
	enqueue_upload(name, client)


@frappe.whitelist(methods=["POST"])
def mark_uploaded(name: str, external_id: str, reason: str) -> None:
	frappe.only_for("DATEV Manager")
	external_id, reason = (external_id or "").strip(), (reason or "").strip()
	if not external_id or not reason:
		frappe.throw(_("The DUo document ID and a reason are required."))
	document = frappe.get_doc(DOCUMENT, name, for_update=True)
	if document.status != "Stuck":
		frappe.throw(_("Only stuck documents can be marked as uploaded."))
	if frappe.db.exists(
		DOCUMENT, {"client": document.client, "external_id": external_id, "name": ("!=", name)}
	):
		frappe.throw(_("The DUo document ID {0} already belongs to another document.").format(external_id))
	document.update(
		{
			"external_id": external_id,
			"program_code": PROGRAM_CODE,
			"status": "Uploaded",
			"message": "",
			"next_attempt": None,
		}
	)
	internal(document).save(ignore_permissions=True)
	log_event(
		DOCUMENT, name, "marked_uploaded", _("Marked as uploaded as {0}: {1}").format(external_id, reason)
	)

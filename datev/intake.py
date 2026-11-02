import frappe
from frappe import _

from datev.beleglink import new_guid
from datev.document_types import DocumentTypeError, resolve
from datev.events import log_event

DOCUMENT = "DATEV Document"


def upsert_document(
	client: str,
	source_key: str,
	*,
	direction: str,
	file_name: str,
	content: bytes,
	document_date,
	document_number: str,
	amount,
	currency: str = "EUR",
	source_doctype: str | None = None,
	source_name: str | None = None,
	is_return: bool = False,
) -> str:
	existing = frappe.db.get_value(DOCUMENT, {"source_key": source_key}, ["name", "status"], as_dict=True)
	if existing and existing.status not in ("Pending", "Stuck"):
		return existing.name

	document = frappe.get_doc(DOCUMENT, existing.name) if existing else frappe.new_doc(DOCUMENT)
	document.update(
		{
			"client": client,
			"source_key": source_key,
			"direction": direction,
			"file_name": file_name,
			"document_date": document_date,
			"document_number": document_number,
			"amount": amount,
			"currency": currency,
			"source_doctype": source_doctype,
			"source_name": source_name,
			"is_return": is_return,
		}
	)
	document.guid = document.guid or new_guid()
	try:
		document.document_type = resolve(
			frappe.get_doc("DATEV Client", client), direction, source_doctype, is_return
		)
		document.status, document.message, document.attempts = "Pending", "", 0
		document.next_attempt = None
	except DocumentTypeError as error:
		document.status, document.message = "Stuck", str(error)
	document.save(ignore_permissions=True)

	replace_file(document, file_name, content)

	if document.status == "Pending":
		frappe.enqueue("datev.documents.upload", name=document.name, enqueue_after_commit=True, queue="short")
	return document.name


def replace_file(document, file_name: str, content: bytes) -> None:
	for old in frappe.get_all(
		"File", filters={"attached_to_doctype": DOCUMENT, "attached_to_name": document.name}, pluck="name"
	):
		frappe.delete_doc("File", old, ignore_permissions=True)
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"is_private": 1,
			"attached_to_doctype": DOCUMENT,
			"attached_to_name": document.name,
		}
	).insert(ignore_permissions=True)
	document.db_set("file", file.file_url)


def discard_document(source_key: str) -> None:
	document = frappe.get_doc(DOCUMENT, {"source_key": source_key})
	if document.status in ("Uploaded", "No Link"):
		frappe.throw(_("The document is already in DATEV. Create a cancellation invoice instead."))
	if document.status == "Discarded":
		return
	document.db_set("status", "Discarded")
	log_event(DOCUMENT, document.name, "discarded")
	for booking in frappe.get_all(
		"DATEV Booking", filters={"document": document.name, "status": "Pending"}, pluck="name"
	):
		frappe.delete_doc("DATEV Booking", booking, ignore_permissions=True)

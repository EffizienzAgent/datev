import base64
import binascii
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import sbool

from datev import intake

ROLES = ("DATEV Manager", "DATEV User")


def _check_access():
	frappe.only_for(ROLES)


def _decimal(value, label: str) -> Decimal:
	try:
		number = Decimal(str(value))
	except InvalidOperation:
		number = None
	if number is None or not number.is_finite():
		frappe.throw(_("{0} must be a number.").format(label))
	return number


def _optional_int(value, label: str) -> int | None:
	if value in (None, ""):
		return None
	try:
		return int(value)
	except TypeError, ValueError:
		frappe.throw(_("{0} must be an integer.").format(label))


def _decode(content_base64: str) -> bytes:
	try:
		return base64.b64decode(content_base64, validate=True)
	except binascii.Error, ValueError, TypeError:
		frappe.throw(_("The file content is not valid base64."))


@frappe.whitelist(methods=["POST"])
def submit_document(
	client,
	source_key,
	direction,
	file_name,
	content_base64,
	document_date,
	document_number,
	amount,
	currency="EUR",
	is_return=0,
	source_doctype=None,
	source_name=None,
):
	_check_access()
	content = _decode(content_base64)
	name = intake.upsert_document(
		client,
		source_key,
		direction=direction,
		file_name=file_name,
		content=content,
		document_date=document_date,
		document_number=document_number,
		amount=_decimal(amount, _("Amount")),
		currency=currency,
		source_doctype=source_doctype,
		source_name=source_name,
		is_return=sbool(is_return),
	)
	return {"name": name, "status": frappe.db.get_value("DATEV Document", name, "status")}


@frappe.whitelist(methods=["POST"])
def submit_party(
	client,
	kind,
	party_name,
	party_type=None,
	party=None,
	vat_id=None,
	iban=None,
	preferred_number=None,
):
	_check_access()
	name = intake.ensure_party_account(
		client,
		kind,
		party_type=party_type or None,
		party=party or None,
		party_name=party_name,
		vat_id=vat_id,
		iban=iban,
		preferred_number=_optional_int(preferred_number, _("Preferred number")),
	)
	return {"name": name}


@frappe.whitelist(methods=["POST"])
def submit_booking(
	document,
	line_key,
	kind,
	party_account,
	contra_account,
	amount,
	debit_credit,
	tax_rate=0,
	bu_key="",
	tax_code=None,
	posting_text="",
	due_date=None,
):
	_check_access()
	name = intake.add_booking(
		document,
		line_key,
		kind=kind,
		party_account=party_account,
		contra_account=_optional_int(contra_account, _("Contra account")),
		amount=_decimal(amount, _("Amount")),
		debit_credit=debit_credit,
		tax_rate=float(_decimal(tax_rate or 0, _("Tax rate"))),
		bu_key=bu_key or "",
		posting_text=posting_text or "",
		due_date=due_date or None,
		tax_code=tax_code or None,
	)
	return {"name": name, "status": frappe.db.get_value("DATEV Booking", name, "status")}


@frappe.whitelist(methods=["GET", "POST"])
def status(source_key):
	_check_access()
	document = frappe.db.get_value(
		"DATEV Document",
		{"source_key": source_key},
		["name", "status", "external_id", "program_code"],
		as_dict=True,
	)
	if not document:
		raise frappe.DoesNotExistError(_("No document with source key {0}.").format(source_key))
	bookings = frappe.get_all(
		"DATEV Booking",
		filters={"document": document.pop("name")},
		fields=["line_key", "status", "batch", "message"],
		order_by="line_key",
	)
	return {"document": document, "bookings": bookings}

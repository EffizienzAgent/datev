import frappe
from frappe import _

DEFAULT_TYPES: dict[str, str] = {
	"Outgoing": "Rechnungsausgang",
	"Incoming": "Rechnungseingang",
	"Other": "Sonstige",
}


class DocumentTypeError(frappe.ValidationError):
	pass


def resolve(client, direction: str, source_doctype: str | None, is_return: bool) -> str:
	document_type = next(
		(
			rule.document_type
			for rule in client.document_type_rules
			if matches(rule, direction, source_doctype, is_return)
		),
		DEFAULT_TYPES[direction],
	)
	allowed = [option.document_type for option in client.document_type_options]
	if allowed and document_type not in allowed:
		raise DocumentTypeError(_("Document type {0} is not defined for this client.").format(document_type))
	return document_type


def matches(rule, direction: str, source_doctype: str | None, is_return: bool) -> bool:
	return (
		rule.direction == direction
		and (not rule.source_doctype or rule.source_doctype == source_doctype)
		and (rule.is_return in (None, "", "Any") or (rule.is_return == "Yes") == is_return)
	)

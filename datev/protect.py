import frappe
from frappe import _


def internal(doc):
	doc.flags.datev_internal = True
	return doc


def reject_manual_changes(doc, fields: tuple[str, ...], state_fields: tuple[str, ...]) -> None:
	if doc.flags.datev_internal:
		return
	if doc.is_new():
		changed = [field for field in state_fields if doc.get(field) not in (None, "", default(doc, field))]
	else:
		changed = [field for field in fields if doc.has_value_changed(field)]
	if changed:
		labels = ", ".join(_(doc.meta.get_label(field)) for field in changed)
		frappe.throw(
			_("These fields are maintained by the DATEV app and cannot be changed: {0}").format(labels)
		)


def default(doc, field: str):
	return doc.meta.get_field(field).default

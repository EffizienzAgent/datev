import frappe

from datev.notify import EXTF_EXPORTS, SENT_STATUSES


def execute():
	for batch in frappe.get_all(
		"DATEV Batch",
		filters={
			"status": ("in", SENT_STATUSES),
			"export_connector": ("in", EXTF_EXPORTS),
			"confirmed_on": ("is", "not set"),
		},
		fields=["name", "sent_on", "modified"],
	):
		frappe.db.set_value(
			"DATEV Batch", batch.name, "confirmed_on", batch.sent_on or batch.modified, update_modified=False
		)

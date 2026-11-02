import frappe

from datev.events import event_label


def execute():
	frappe.local.lang = frappe.db.get_single_value("System Settings", "language") or "en"
	for event in frappe.get_all(
		"DATEV Event Log", filters={"label": ("is", "not set")}, pluck="event", distinct=True
	):
		frappe.db.set_value(
			"DATEV Event Log",
			{"event": event, "label": ("is", "not set")},
			"label",
			event_label(event),
			update_modified=False,
		)

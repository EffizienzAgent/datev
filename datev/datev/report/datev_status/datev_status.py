import frappe
from frappe import _
from frappe.utils import now_datetime

from datev import dispatch, notify

CLIENT = "DATEV Client"
PROBLEM_FIELDS = (
	"awaiting_approval",
	"sending_batches",
	"stuck_bookings",
	"failed_bookings",
	"stuck_documents",
	"parties_to_clarify",
	"party_errors",
	"overdue",
	"unconfirmed_overdue",
)


def get_columns() -> list[dict]:
	def column(fieldname, label, fieldtype="Int", **extra):
		return {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": 140, **extra}

	return [
		column("client", _("Client"), "Link", options=CLIENT, width=220),
		column("transferred_30d", _("Transferred (30 days)")),
		column("awaiting_approval", _("Awaiting Approval")),
		column("sending_batches", _("Sending Batches")),
		column("failed_batches", _("Failed Batches (30 days)")),
		column("unconfirmed_batches", _("EXTF Files Not Confirmed")),
		column("stuck_bookings", _("Stuck Bookings")),
		column("failed_bookings", _("Failed Bookings")),
		column("stuck_documents", _("Stuck Documents")),
		column("documents_without_link", _("Documents without Link")),
		column("held", _("Held")),
		column("waiting_bookings", _("Waiting for Dependency")),
		column("parties_to_clarify", _("Parties to Clarify")),
		column("party_errors", _("Party Errors")),
		column("overdue", _("Overdue"), "Check", width=90),
		column("bos_credits", _("Business OS Credits")),
	]


def has_problem(row: dict, client) -> bool:
	return any(row[field] for field in PROBLEM_FIELDS) or notify.low_credits(client)


def execute(filters=None):
	now = now_datetime()
	rows = []
	problems = {}
	for name in frappe.get_all(CLIENT, pluck="name", order_by="name asc"):
		client = frappe.get_doc(CLIENT, name)
		is_business_os = client.storage_connector == "Business OS"
		row = {
			"client": name,
			**notify.status_counts(name, now),
			"overdue": int(client.enabled and dispatch.is_overdue(client, now)),
			"bos_credits": client.bos_credits if is_business_os else None,
			"enabled": client.enabled,
		}
		problems[name] = has_problem(row, client)
		rows.append(row)
	rows.sort(key=lambda row: (not row["enabled"], not problems[row["client"]]))
	return get_columns(), rows

import frappe
from frappe import _
from frappe.utils import now_datetime

from datev import dispatch, notify

CLIENT = "DATEV Client"
PROBLEM_FIELDS = (
	"awaiting_approval",
	"sending_batches",
	"stuck_bookings",
	"stuck_documents",
	"parties_to_clarify",
	"overdue",
)


def get_columns() -> list[dict]:
	def column(fieldname, label, fieldtype="Int", **extra):
		return {"fieldname": fieldname, "label": _(label), "fieldtype": fieldtype, "width": 140, **extra}

	return [
		column("client", "Client", "Link", options=CLIENT, width=220),
		column("transferred_30d", "Transferred (30 days)"),
		column("awaiting_approval", "Awaiting Approval"),
		column("sending_batches", "Sending Batches"),
		column("stuck_bookings", "Stuck Bookings"),
		column("stuck_documents", "Stuck Documents"),
		column("documents_without_link", "Documents without Link"),
		column("held", "Held"),
		column("parties_to_clarify", "Parties to Clarify"),
		column("overdue", "Overdue", "Check", width=90),
		column("bos_credits", "Business OS Credits"),
	]


def has_problem(row: dict, client) -> bool:
	return any(row[field] for field in PROBLEM_FIELDS) or notify.low_credits(client)


def execute(filters=None):
	now = now_datetime()
	rows = []
	problems = {}
	for name in frappe.get_all(CLIENT, filters={"enabled": 1}, pluck="name", order_by="name asc"):
		client = frappe.get_doc(CLIENT, name)
		is_business_os = client.storage_connector == "Business OS"
		row = {
			"client": name,
			**notify.status_counts(name, now),
			"overdue": int(dispatch.is_overdue(client, now)),
			"bos_credits": client.bos_credits if is_business_os else None,
		}
		problems[name] = has_problem(row, client)
		rows.append(row)
	rows.sort(key=lambda row: not problems[row["client"]])
	return get_columns(), rows

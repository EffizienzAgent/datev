import frappe

ROLES = ("DATEV Manager", "DATEV User")


def apply_defaults() -> None:
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)

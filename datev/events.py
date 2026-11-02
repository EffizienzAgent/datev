import json

import frappe

MAX_RESPONSE_LENGTH = 20000


def log_event(
	reference_doctype: str, reference_name: str, event: str, message: str = "", response: object = None
) -> None:
	serialized = None
	if response is not None:
		serialized = json.dumps(response, default=str)[:MAX_RESPONSE_LENGTH]
	frappe.get_doc(
		{
			"doctype": "DATEV Event Log",
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"event": event,
			"message": message,
			"response": serialized,
		}
	).insert(ignore_permissions=True)

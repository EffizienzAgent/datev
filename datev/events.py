import json

import frappe
from frappe import _

MAX_RESPONSE_LENGTH = 20000


def event_label(event: str) -> str:
	labels = {
		"approved": _("Approved"),
		"sent": _("Sent"),
		"partially_sent": _("Partially Sent"),
		"failed": _("Failed"),
		"delivery_uncertain": _("Delivery uncertain"),
		"resolved_delivered": _("Resolved as delivered"),
		"resolved_not_delivered": _("Resolved as not delivered"),
		"file_ready": _("File ready"),
		"import_confirmed": _("Import confirmed"),
		"resent": _("E-mail sent again"),
		"reset": _("Reset to pending"),
		"uploaded": _("Uploaded"),
		"upload_retry": _("Upload will be retried"),
		"upload_failed": _("Upload failed"),
		"marked_uploaded": _("Marked as uploaded"),
		"discarded": _("Discarded"),
		"open_bookings_discarded": _("Open bookings discarded"),
		"retried": _("Retried"),
		"retry": _("Will be retried"),
		"created": _("Created"),
		"imported": _("Imported"),
		"to_clarify": _("To Clarify"),
		"resolved": _("Marked resolved"),
		"error": _("Error"),
	}
	return labels.get(event, event)


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
			"label": event_label(event),
			"message": message,
			"response": serialized,
		}
	).insert(ignore_permissions=True)

from frappe.model.document import Document

from datev.protect import reject_manual_changes

PROTECTED = ("status", "confirmed_on", "confirmed_by")


class DATEVBatch(Document):
	def validate(self):
		reject_manual_changes(self, PROTECTED, PROTECTED)

from frappe.model.document import Document

from datev.protect import reject_manual_changes

PROTECTED = ("status", "external_id", "guid", "program_code")


class DATEVDocument(Document):
	def validate(self):
		reject_manual_changes(self, PROTECTED, PROTECTED)

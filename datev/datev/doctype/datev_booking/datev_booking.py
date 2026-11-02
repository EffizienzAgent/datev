import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from datev.fiscal import fiscal_year_id


class DATEVBooking(Document):
	def validate(self):
		self.validate_amount()
		self.validate_line_key()
		self.set_fiscal_year()

	def validate_amount(self):
		if (self.amount or 0) <= 0:
			frappe.throw(_("Amount must be greater than zero."))

	def validate_line_key(self):
		duplicate = frappe.db.exists(
			"DATEV Booking",
			{"document": self.document, "line_key": self.line_key, "name": ("!=", self.name)},
		)
		if duplicate:
			frappe.throw(_("Line key {0} already exists for this document.").format(self.line_key))

	def set_fiscal_year(self):
		if self.document_date:
			self.fiscal_year = fiscal_year_id(self.client, getdate(self.document_date))

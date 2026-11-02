import re

import frappe
from frappe import _
from frappe.model.document import Document

BU_KEY_PATTERN = re.compile(r"^\d{0,4}$")
DC = "DATEVconnect"


class DATEVClient(Document):
	def validate(self):
		self.validate_ranges()
		self.validate_connectors()
		self.validate_numbering()
		self.validate_tax_keys()
		self.validate_account_mappings()

	def validate_ranges(self):
		if not 4 <= (self.account_length or 0) <= 8:
			frappe.throw(_("Account length must be between 4 and 8."))
		if not 1 <= (self.fiscal_year_start_month or 0) <= 12:
			frappe.throw(_("Fiscal year start month must be between 1 and 12."))
		if not 1 <= (self.dispatch_day_of_month or 0) <= 28:
			frappe.throw(_("Dispatch day of month must be between 1 and 28."))

	def validate_connectors(self):
		if self.storage_connector == "Business OS":
			self.require("bos_api_key", "bos_connection_id")
		elif self.storage_connector == "E-mail to DUo":
			self.require("duo_email")
		if DC in (self.export_connector, self.master_data_connector):
			self.require("dc_base_url", "dc_username", "dc_password")
		if self.export_connector == "EXTF e-mail":
			self.require("extf_email")

	def require(self, *fieldnames):
		for fieldname in fieldnames:
			if not self.get(fieldname):
				label = _(self.meta.get_label(fieldname))
				frappe.throw(_("{0} is required for the selected connector.").format(label))

	def validate_numbering(self):
		if self.master_data_connector == "EXTF" and "DATEV next available" in (
			self.debtor_numbering,
			self.creditor_numbering,
		):
			frappe.throw(_("Numbering by DATEV requires the DATEVconnect master data connector."))

	def validate_tax_keys(self):
		for row in self.tax_keys:
			if not BU_KEY_PATTERN.match(row.bu_key or ""):
				frappe.throw(_("Row {0}: BU key must have at most four digits.").format(row.idx))
		self.reject_duplicates(self.tax_keys, "tax_code", _("Tax code"))

	def validate_account_mappings(self):
		self.reject_duplicates(self.account_mappings, "source_account", _("Source account"))

	@staticmethod
	def reject_duplicates(rows, fieldname, label):
		seen = set()
		for row in rows:
			value = row.get(fieldname)
			if value in seen:
				frappe.throw(_("Row {0}: {1} {2} is listed more than once.").format(row.idx, label, value))
			seen.add(value)

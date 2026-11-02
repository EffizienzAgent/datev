import frappe
from frappe import _
from frappe.model.document import Document

CREDITOR_FIRST_GROUP = 7


class DATEVPartyAccount(Document):
	def validate(self):
		self.validate_unique_number()
		self.validate_unique_party()
		self.validate_number_range()
		self.validate_settled_number()

	def validate_settled_number(self):
		if self.sync_status in ("Imported", "Created") and not (self.account_number or 0) > 0:
			frappe.throw(_("Imported or created party accounts need an account number."))

	def validate_unique_number(self):
		if self.account_number:
			self.reject_existing(
				{"kind": self.kind, "account_number": self.account_number},
				_("Account number {0} is already in use.").format(self.account_number),
			)

	def validate_unique_party(self):
		if self.party_type and self.party:
			self.reject_existing(
				{"kind": self.kind, "party_type": self.party_type, "party": self.party},
				_("This party already has an account."),
			)

	def reject_existing(self, filters, message):
		filters = {**filters, "client": self.client, "name": ("!=", self.name)}
		if frappe.db.exists("DATEV Party Account", filters):
			frappe.throw(message)

	def validate_number_range(self):
		if not self.account_number:
			return
		length = frappe.db.get_value("DATEV Client", self.client, "account_length") or 4
		base = 10**length
		low, high = (
			(base, CREDITOR_FIRST_GROUP * base - 1)
			if self.kind == "Debtor"
			else (CREDITOR_FIRST_GROUP * base, 10 * base - 1)
		)
		if not low <= self.account_number <= high:
			frappe.throw(
				_("{0} account numbers must be between {1} and {2}.").format(_(self.kind), low, high)
			)

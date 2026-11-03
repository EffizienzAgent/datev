import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from datev.fiscal import fiscal_year_id
from datev.protect import reject_manual_changes

PROTECTED = (
	"status",
	"batch",
	"amount",
	"party_account",
	"account",
	"contra_account",
	"document",
	"document_field_1",
	"depends_on_document",
	"general_reversal",
	"held",
	"waiting",
)
STATE = ("status", "batch", "held", "waiting")
PARTY_KINDS = {"Outgoing Invoice": "Debtor", "Incoming Invoice": "Creditor"}
INVOICE_KINDS = tuple(PARTY_KINDS)


class DATEVBooking(Document):
	def validate(self):
		reject_manual_changes(self, PROTECTED, STATE)
		self.validate_amount()
		self.validate_party_or_account()
		self.validate_party_account()
		self.validate_line_key()
		self.validate_dependency()
		self.set_fiscal_year()

	def validate_amount(self):
		if (self.amount or 0) <= 0:
			frappe.throw(_("Amount must be greater than zero."))

	def validate_party_or_account(self):
		if bool(self.party_account) == bool(self.account):
			frappe.throw(_("A booking needs either a party account or an account, not both."))
		if self.account and self.kind != "Payment":
			frappe.throw(_("{0} bookings need a party account.").format(_(self.kind)))

	def validate_party_account(self):
		if not self.party_account:
			return
		party = frappe.db.get_value(
			"DATEV Party Account", self.party_account, ["client", "kind"], as_dict=True
		)
		if not party or party.client != self.client:
			frappe.throw(_("Party account {0} belongs to another client.").format(self.party_account))
		expected = PARTY_KINDS.get(self.kind)
		if expected and party.kind != expected:
			frappe.throw(_("{0} bookings need a {1} party account.").format(_(self.kind), _(expected)))

	def validate_line_key(self):
		duplicate = frappe.db.exists(
			"DATEV Booking",
			{"document": self.document, "line_key": self.line_key, "name": ("!=", self.name)},
		)
		if duplicate:
			frappe.throw(_("Line key {0} already exists for this document.").format(self.line_key))

	def validate_dependency(self):
		depended_on = frappe.db.exists(
			"DATEV Booking", {"depends_on_document": self.document, "name": ("!=", self.name)}
		)
		if depended_on and (self.kind not in INVOICE_KINDS or self.depends_on_document):
			frappe.throw(
				_(
					"Other bookings depend on this document, so it only takes invoice bookings without dependency."
				)
			)
		target = self.depends_on_document
		if not target:
			return
		client = frappe.db.get_value("DATEV Document", target, "client")
		if target == self.document or client != self.client:
			frappe.throw(
				_("A booking can only depend on another document of the same client: {0}").format(target)
			)
		if frappe.db.exists(
			"DATEV Booking",
			{"document": target, "kind": ("not in", INVOICE_KINDS), "name": ("!=", self.name)},
		) or frappe.db.exists("DATEV Booking", {"document": target, "depends_on_document": ("is", "set")}):
			frappe.throw(
				_(
					"A booking can only depend on a document with invoice bookings that depend on nothing: {0}"
				).format(target)
			)

	def set_fiscal_year(self):
		if self.document_date:
			self.fiscal_year = fiscal_year_id(self.client, getdate(self.document_date))

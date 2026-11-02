import frappe
from frappe import _
from frappe.utils import now_datetime

from datev.connectors.base import (
	ConnectorError,
	ExportConnector,
	ExportItemResult,
	ExportResult,
	MasterDataConnector,
	RemoteParty,
)
from datev.extf import booking_batch, master_data
from datev.extf.format import yyyymmdd


class ExtfDownloadExport(ExportConnector):
	final_status = "Exported"

	def export(self, batch, bookings: list) -> ExportResult:
		files = self.build_files(batch, bookings)
		urls = [self.save_file(batch, name, content) for name, content in files]
		frappe.db.set_value("DATEV Batch", batch.name, "file", urls[0])
		batch.file = urls[0]
		self.deliver(batch, files)
		return ExportResult(
			items=[ExportItemResult(booking.name, True) for booking in bookings],
			files=urls,
		)

	def build_files(self, batch, bookings: list) -> list[tuple[str, bytes]]:
		created = now_datetime()
		files = booking_batch.build(self.client, bookings, created, batch.name)
		parties = frappe.get_all(
			"DATEV Party Account",
			filters={
				"name": ("in", {booking.party_account for booking in bookings}),
				"sync_status": "New",
			},
			fields=["name", "account_number", "party_name", "vat_id", "iban"],
			order_by="account_number asc",
		)
		if parties:
			name = f"EXTF_Debitoren_Kreditoren_{self.client.client_number}_{yyyymmdd(created.date())}.csv"
			files.append((name, master_data.build(self.client, parties, created)))
		return files

	@staticmethod
	def save_file(batch, name: str, content: bytes) -> str:
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": name,
				"content": content,
				"is_private": 1,
				"attached_to_doctype": "DATEV Batch",
				"attached_to_name": batch.name,
			}
		).insert(ignore_permissions=True)
		return file.file_url

	def deliver(self, batch, files: list[tuple[str, bytes]]) -> None:
		pass


class ExtfEmailExport(ExtfDownloadExport):
	def export(self, batch, bookings: list) -> ExportResult:
		if not self.client.extf_email:
			raise ConnectorError(_("No EXTF e-mail address is set for this client."))
		return super().export(batch, bookings)

	def deliver(self, batch, files: list[tuple[str, bytes]]) -> None:
		frappe.sendmail(
			recipients=[self.client.extf_email],
			subject=_("DATEV booking batch {0}").format(batch.name),
			message=_("The EXTF files of DATEV booking batch {0} are attached.").format(batch.name),
			attachments=[{"fname": name, "fcontent": content} for name, content in files],
		)


class ExtfMasterData(MasterDataConnector):
	creates_via_export = True
	can_fetch = False

	def fetch_parties(self) -> list[RemoteParty]:
		raise ConnectorError(_("Upload a master data export from DATEV to match existing accounts."))

	def next_number(self, kind: str) -> int:
		raise ConnectorError(_("Account numbers cannot be requested from DATEV via EXTF."))

	def create_party(self, party_account) -> None:
		return None

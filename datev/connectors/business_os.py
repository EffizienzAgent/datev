import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from datev.clients.business_os import BusinessOS, BusinessOSError
from datev.connectors.base import StorageConnector, StorageResult, read_document_file

PROGRAM_CODE = "BEDI"


class BusinessOSStorage(StorageConnector):
	min_interval = 5

	def api(self) -> BusinessOS:
		return BusinessOS(self.client.get_password("bos_api_key"), self.client.bos_connection_id)

	def upload(self, document) -> StorageResult:
		content = read_document_file(document)
		send_own_id = self.client.bos_id_mode == "Send own ID"
		try:
			response = self.api().upload_document(
				content,
				document.file_name or document.file,
				document.document_type,
				id=document.guid if send_own_id else None,
			)
		except BusinessOSError as error:
			if error.maybe_sent and not send_own_id:
				raise BusinessOSError(
					_("Upload may have reached DATEV. Check DUo before retrying."), response=error.response
				) from error
			raise
		self.store_credits(response)
		if send_own_id and str(response["id"]).casefold() != document.guid.casefold():
			raise BusinessOSError(
				_(
					"Business OS stored the document as {0} instead of {1}, so the document link "
					"would be wrong. Check DUo, then mark the document as uploaded."
				).format(response["id"], document.guid),
				response=response,
			)
		external_id = document.guid if send_own_id else response["id"]
		return StorageResult(external_id=external_id, program_code=PROGRAM_CODE, response=response)

	def store_credits(self, response) -> None:
		try:
			info = response.get("business-os") if isinstance(response, dict) else None
			credits_left = info.get("neuesGuthaben") if isinstance(info, dict) else None
			if credits_left is None:
				return
			frappe.db.set_value(
				"DATEV Client",
				self.client.name,
				{"bos_credits": cint(credits_left), "bos_credits_updated_on": now_datetime()},
			)
		except Exception:
			frappe.log_error(title="DATEV Business OS credits not stored")

	def document_types(self) -> list[dict]:
		return self.api().document_types()

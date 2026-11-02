import frappe

from datev.clients.business_os import BusinessOS
from datev.connectors.base import StorageConnector, StorageResult, read_document_file

PROGRAM_CODE = "BEDI"


class BusinessOSStorage(StorageConnector):
	def api(self) -> BusinessOS:
		return BusinessOS(self.client.get_password("bos_api_key"), self.client.bos_connection_id)

	def upload(self, document) -> StorageResult:
		content = read_document_file(document)
		send_own_id = self.client.bos_id_mode == "Send own ID"
		response = self.api().upload_document(
			content,
			document.file_name or document.file,
			document.document_type,
			id=document.guid if send_own_id else None,
		)
		self.store_credits(response)
		external_id = document.guid if send_own_id else response["id"]
		return StorageResult(external_id=external_id, program_code=PROGRAM_CODE, response=response)

	def store_credits(self, response: dict):
		credits_left = (response.get("business-os") or {}).get("neuesGuthaben")
		if credits_left is not None:
			frappe.db.set_value("DATEV Client", self.client.name, "bos_credits", credits_left)

	def document_types(self) -> list[dict]:
		return self.api().document_types()

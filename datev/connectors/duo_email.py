import frappe

from datev.connectors.base import StorageConnector, StorageResult, read_document_file


class DuoEmailStorage(StorageConnector):
	def upload(self, document) -> StorageResult:
		content = read_document_file(document)
		file_name = document.file_name or document.file.rsplit("/", 1)[-1]
		frappe.sendmail(
			recipients=[self.client.duo_email],
			subject=file_name,
			attachments=[{"fname": file_name, "fcontent": content}],
			now=True,
		)
		return StorageResult(None, None)

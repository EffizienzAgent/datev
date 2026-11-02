from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from datev import demo


class TestDemo(IntegrationTestCase):
	def test_create_is_rerunnable(self):
		with patch.object(frappe.db, "commit"):
			first = demo.create()
			second = demo.create()
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("DATEV Client", {"title": demo.TITLE}), 1)
		self.assertTrue(frappe.db.exists("DATEV Batch", {"client": first, "status": "Awaiting Approval"}))
		self.assertTrue(frappe.db.exists("DATEV Batch", {"client": first, "status": "Sent"}))
		self.assertTrue(
			frappe.db.exists("DATEV Party Account", {"client": first, "sync_status": "To Clarify"})
		)

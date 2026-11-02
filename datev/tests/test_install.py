import frappe
from frappe.tests import IntegrationTestCase


class TestInstall(IntegrationTestCase):
	def test_app_installed(self):
		self.assertIn("datev", frappe.get_installed_apps())

	def test_roles_created(self):
		for role in ("DATEV Manager", "DATEV User"):
			self.assertTrue(frappe.db.exists("Role", role))

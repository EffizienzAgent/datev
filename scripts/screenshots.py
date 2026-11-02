"""Screenshots of a demo site for docs/images.

Needs Playwright (pip install playwright, then playwright install chromium) and a site filled with
bench --site <site> execute datev.demo.create. Settings come from the environment:

- DATEV_BASE: site URL, default http://localhost:8010
- DATEV_USER: default Administrator
- DATEV_PASSWORD: required
- DATEV_SHOTS: output folder, default docs/images

Pass screen names (e.g. client-form batch) to take only those.
"""

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DATEV_BASE", "http://localhost:8010")
OUT = Path(os.environ.get("DATEV_SHOTS", "docs/images"))
VIEWPORT = {"width": 1440, "height": 1250}


def first(page, doctype, filters, field="name"):
	rows = page.evaluate(
		"([d, f, x]) => frappe.xcall('frappe.client.get_list', {doctype: d, filters: f, fields: [x], limit_page_length: 1, order_by: 'creation desc'})",
		[doctype, filters, field],
	)
	if not rows:
		raise LookupError(f"No {doctype} matches {filters}. Is this a datev.demo.create site?")
	return rows[0][field]


def shot(page, name, wait=2500):
	page.wait_for_timeout(wait)
	page.screenshot(path=str(OUT / f"{name}.png"))
	print("saved", name)


def open_form(page, route, name):
	page.goto(f"{BASE}/app/{route}/{name}")
	page.wait_for_selector(".form-layout", timeout=20000)


def login(page):
	page.goto(f"{BASE}/login")
	page.evaluate(
		"""([usr, pwd]) => fetch('/api/method/login', {method: 'POST',
			headers: {'Content-Type': 'application/json'}, body: JSON.stringify({usr, pwd})})""",
		[os.environ.get("DATEV_USER", "Administrator"), os.environ["DATEV_PASSWORD"]],
	)
	page.goto(f"{BASE}/app/datev")
	page.wait_for_selector(".desk-sidebar, .layout-main", timeout=20000)
	page.evaluate("frappe.db.set_value('User', frappe.session.user, 'language', 'en')")


class Screens:
	def __init__(self, page):
		self.page = page

	def workspace(self):
		self.page.goto(f"{BASE}/app/datev")
		shot(self.page, "workspace", 4000)

	def client_form(self):
		open_form(self.page, "datev-client", first(self.page, "DATEV Client", {}))
		self.page.evaluate(
			"document.querySelector(\"[data-fieldname='connectors_section']\").scrollIntoView(); window.scrollBy(0, -80)"
		)
		shot(self.page, "client-form")

	def client_dispatch(self):
		open_form(self.page, "datev-client", first(self.page, "DATEV Client", {}))
		self.page.evaluate("document.querySelector(\"[data-fieldname='dispatch_section']\").scrollIntoView()")
		shot(self.page, "client-dispatch")

	def batch_awaiting_approval(self):
		open_form(self.page, "datev-batch", first(self.page, "DATEV Batch", {"status": "Awaiting Approval"}))
		shot(self.page, "batch-awaiting-approval")

	def batch_bookings(self):
		batch = first(self.page, "DATEV Batch", {"status": "Awaiting Approval"})
		self.page.goto(f"{BASE}/app/datev-booking/view/report?batch={batch}")
		self.page.wait_for_selector(".datatable", timeout=20000)
		shot(self.page, "batch-bookings", 3500)

	def batch_exported(self):
		open_form(self.page, "datev-batch", first(self.page, "DATEV Batch", {"status": "Sent"}))
		shot(self.page, "batch-exported")

	def booking_form(self):
		open_form(
			self.page,
			"datev-booking",
			first(self.page, "DATEV Booking", {"posting_text": "Rechnung RE-2026-1043"}),
		)
		shot(self.page, "booking-form")

	def document_form(self):
		open_form(
			self.page,
			"datev-document",
			first(self.page, "DATEV Document", {"document_number": "RE-2026-1041"}),
		)
		shot(self.page, "document-form")

	def documents(self):
		self.page.goto(f"{BASE}/app/datev-document")
		self.page.wait_for_selector(".frappe-list", timeout=20000)
		shot(self.page, "documents", 3500)

	def status_report(self):
		self.page.goto(f"{BASE}/app/query-report/DATEV Status")
		self.page.wait_for_selector(".datatable", timeout=20000)
		self.page.set_viewport_size({"width": 2400, "height": 500})
		self.page.goto(f"{BASE}/app/query-report/DATEV Status")
		self.page.wait_for_selector(".datatable", timeout=20000)
		shot(self.page, "status-report", 5000)
		self.page.set_viewport_size(VIEWPORT)

	def party_to_clarify(self):
		open_form(
			self.page,
			"datev-party-account",
			first(self.page, "DATEV Party Account", {"sync_status": "To Clarify"}),
		)
		shot(self.page, "party-to-clarify")


def main(only):
	OUT.mkdir(parents=True, exist_ok=True)
	with sync_playwright() as playwright:
		browser = playwright.chromium.launch()
		page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
		login(page)
		screens = Screens(page)
		for name in [n for n in vars(Screens) if not n.startswith("_")]:
			if not only or name.replace("_", "-") in only:
				getattr(screens, name)()
		browser.close()


if __name__ == "__main__":
	main(set(sys.argv[1:]))

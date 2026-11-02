from datetime import date

import frappe


def fiscal_year_start(client, on: date) -> date:
	month = frappe.db.get_value("DATEV Client", client, "fiscal_year_start_month") or 1
	year = on.year if on.month >= month else on.year - 1
	return date(year, month, 1)


def fiscal_year_id(client, on: date) -> str:
	return fiscal_year_start(client, on).strftime("%Y%m%d")

import re
from datetime import date, datetime

from datev.extf.format import text, yyyymmdd

BOOKING_BATCH = 21
DESCRIPTION_INVALID = re.compile(r"[^\w.\-/ ]")


def _date(value: date | None) -> str:
	return yyyymmdd(value) if value else ""


def row(
	client,
	*,
	category: int,
	format_name: str,
	format_version: int,
	created: datetime,
	fiscal_year_start: date | None,
	date_from: date | None,
	date_to: date | None,
	description: str,
) -> str:
	is_batch = category == BOOKING_BATCH
	fields = [
		'"EXTF"',
		"700",
		str(category),
		text(format_name),
		str(format_version),
		created.strftime("%Y%m%d%H%M%S") + f"{created.microsecond // 1000:03d}",
		"",
		'"RE"',
		'""',
		'""',
		str(client.consultant_number),
		str(client.client_number),
		_date(fiscal_year_start),
		str(client.account_length or 4),
		_date(date_from),
		_date(date_to),
		text(DESCRIPTION_INVALID.sub("", description or ""), 30),
		'""',
		"1" if is_batch else "",
		"0" if is_batch else "",
		"0" if is_batch else "",
		'"EUR"' if is_batch else '""',
		"",
		'""',
		"",
		"",
		text(client.chart[-2:]),
		"",
		"",
		'""',
		'""',
	]
	return ";".join(fields)

import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ENCODING = "cp1252"
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
DOCUMENT_FIELD_INVALID = re.compile(r"[^A-Za-z0-9$&%*+\-/]")


def _encodable(char: str) -> bool:
	try:
		char.encode(ENCODING)
	except UnicodeEncodeError:
		return False
	return True


def _fold(value: str, fallback: str) -> str:
	result = []
	for char in value:
		if _encodable(char):
			result.append(char)
			continue
		for part in unicodedata.normalize("NFKD", char):
			if not unicodedata.combining(part):
				result.append(part if _encodable(part) else fallback)
	return "".join(result)


def text(value: str | None, max_len: int | None = None) -> str:
	value = _fold(CONTROL_CHARS.sub(" ", value or ""), "")
	value = " ".join(value.split())
	if max_len:
		value = value[:max_len]
	return '"' + value.replace('"', '""') + '"'


def document_field(value: str) -> str:
	return DOCUMENT_FIELD_INVALID.sub("", value or "")[:36]


def amount(value) -> str:
	value = abs(Decimal(str(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	return f"{value:.2f}".replace(".", ",")


def ddmm(d: date) -> str:
	return d.strftime("%d%m")


def yyyymmdd(d: date) -> str:
	return d.strftime("%Y%m%d")


def encode(lines: list[str]) -> bytes:
	return _fold("\r\n".join(lines) + "\r\n", "?").encode(ENCODING)

from dataclasses import dataclass

import requests
from urllib3.exceptions import NewConnectionError


class TransportError(Exception):
	def __init__(self, message: str, *, maybe_sent: bool = False):
		super().__init__(message)
		self.maybe_sent = maybe_sent


def maybe_sent(error: requests.RequestException) -> bool:
	if isinstance(error, requests.ConnectTimeout | requests.exceptions.SSLError):
		return False
	if isinstance(error, requests.ConnectionError):
		return not any(isinstance(cause, NewConnectionError) for cause in causes(error))
	return True


def causes(error: BaseException):
	pending, seen = [error], set()
	while pending:
		current = pending.pop()
		if not isinstance(current, BaseException) or id(current) in seen:
			continue
		seen.add(id(current))
		yield current
		pending.extend(current.args)
		pending.extend((getattr(current, "reason", None), current.__cause__, current.__context__))


@dataclass
class HttpResponse:
	status: int
	data: object | None
	text: str


def request(
	method: str,
	url: str,
	*,
	headers=None,
	params=None,
	json=None,
	auth=None,
	verify: bool = True,
	timeout: int = 30,
) -> HttpResponse:
	try:
		response = requests.request(
			method, url, headers=headers, params=params, json=json, auth=auth, verify=verify, timeout=timeout
		)
	except requests.RequestException as e:
		raise TransportError(str(e), maybe_sent=maybe_sent(e)) from e

	try:
		data = response.json()
	except ValueError:
		data = None
	return HttpResponse(status=response.status_code, data=data, text=response.text)

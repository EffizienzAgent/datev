from dataclasses import dataclass

import requests


class TransportError(Exception):
	def __init__(self, message: str, *, maybe_sent: bool = False):
		super().__init__(message)
		self.maybe_sent = maybe_sent


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
		raise TransportError(str(e), maybe_sent=isinstance(e, requests.ReadTimeout)) from e

	try:
		data = response.json()
	except ValueError:
		data = None
	return HttpResponse(status=response.status_code, data=data, text=response.text)

from dataclasses import dataclass

import requests


class TransportError(Exception):
	pass


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
		raise TransportError(str(e)) from e

	try:
		data = response.json()
	except ValueError:
		data = None
	return HttpResponse(status=response.status_code, data=data, text=response.text)

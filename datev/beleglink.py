import uuid


def new_guid() -> str:
	return str(uuid.uuid4())


def extf_value(program_code: str, external_id: str) -> str:
	return f'{program_code} "{external_id}"'

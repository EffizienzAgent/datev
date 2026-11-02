# Development

Requires Docker.

```
docker compose -f dev/compose.yaml up -d
docker compose -f dev/compose.yaml exec bench bash /workspace/frappe-datev/dev/bootstrap.sh
dev/test.sh
```

The bench is served at http://localhost:8010 (site `datev.test`, user `Administrator`, password `admin`).

Run one module with `dev/test.sh --module datev.tests.test_dispatch`. HTTP is mocked by patching `datev.http.requests.request`; no test calls DATEV or Business OS. Use DATEV's sample numbers (consultant 29098, client 55003) in tests.

Lint:

```
docker compose -f dev/compose.yaml exec bench bash -lc "cd /workspace/frappe-datev && ~/bench-data/frappe-bench/env/bin/ruff check . && ~/bench-data/frappe-bench/env/bin/ruff format --check ."
```

## Translations

User-facing strings are wrapped in `_()` (Python) and `__()` (JavaScript); doctype metadata is extracted by Frappe. German is in `datev/locale/de.po`. After changing strings:

```
docker compose -f dev/compose.yaml exec bench bash -lc "cd frappe-bench && bench --site datev.test generate-pot-file --app datev && bench --site datev.test update-po-files --app datev --locale de"
```

Then translate the new empty entries in `de.po` with DATEV terminology (Mandant, Beleg, Buchungsstapel, Personenkonto, BU-Schlüssel, Stornorechnung). `datev.tests.test_translations` fails when the template is outdated or an entry is untranslated.

`babel_extractors.csv` routes `hooks.py` to the plain Python extractor, because Frappe's navbar extractor fails on a soft-linked app as used in the dev bench.

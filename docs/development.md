# Development

Requires Docker.

```
docker compose -f dev/compose.yaml up -d
docker compose -f dev/compose.yaml exec bench bash /workspace/frappe-datev/dev/bootstrap.sh
dev/test.sh
```

The bench is served at http://localhost:8010 (site `datev.test`, user `Administrator`, password `admin`).

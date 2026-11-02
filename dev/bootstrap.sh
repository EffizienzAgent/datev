#!/usr/bin/env bash
# Run inside the bench container:
#   docker compose -f dev/compose.yaml exec bench bash /workspace/frappe-datev/dev/bootstrap.sh
set -euo pipefail

BENCH_ROOT=/home/frappe/bench-data
BENCH_DIR=$BENCH_ROOT/frappe-bench
APP_DIR=/workspace/frappe-datev
SITE=datev.test

sudo chown frappe:frappe "$BENCH_ROOT"
cd "$BENCH_ROOT"

if [ ! -d "$BENCH_DIR/apps/frappe" ]; then
  bench init --frappe-branch version-16 --skip-redis-config-generation --skip-assets frappe-bench
fi

cd "$BENCH_DIR"
bench set-config -g db_host db
bench set-config -g redis_cache redis://redis-cache:6379
bench set-config -g redis_queue redis://redis-queue:6379
bench set-config -g redis_socketio redis://redis-queue:6379

if [ ! -d apps/datev ]; then
  bench get-app --soft-link "$APP_DIR"
fi

if [ ! -d "sites/$SITE" ]; then
  bench new-site "$SITE" --db-root-password 123 --admin-password admin --mariadb-user-host-login-scope=%
fi

bench --site "$SITE" list-apps | grep -qx datev || bench --site "$SITE" install-app datev
bench --site "$SITE" set-config allow_tests true
bench --site "$SITE" set-config developer_mode 1

[ -d sites/assets/frappe/dist ] || bench build

#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys


PA_HOST = os.environ.get("PA_HOST", "ssh.pythonanywhere.com")
PA_USERNAME = os.environ.get("PA_USERNAME")
if not PA_USERNAME:
    raise SystemExit("PA_USERNAME is required")

remote_script = r'''set -euo pipefail

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_cmd python3
need_cmd mysql
need_cmd mysqldump
need_cmd gzip

MAIN_ROOT=/home/techhub/techhub-dns
DEV_ROOT=/home/techhub/techhub-dns-dev
MAIN_ENV="$MAIN_ROOT/backend/.env"
DEV_ENV="$DEV_ROOT/backend/.env"
BACKUP_DIR="$HOME/db-sync-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

python3 - "$MAIN_ENV" "$DEV_ENV" "$BACKUP_DIR" <<'PY'
from pathlib import Path
from urllib.parse import urlparse, unquote
import os
import re
import shlex
import subprocess
import sys

main_env, dev_env, backup_dir = sys.argv[1:4]


def load_db_url(env_path: str) -> str:
    text = Path(env_path).read_text()
    match = re.search(r'^DATABASE_URL=(.+)$', text, re.M)
    if not match:
        raise SystemExit(f'Missing DATABASE_URL in {env_path}')
    value = match.group(1).strip()
    if value and value[0] in '"\'' and value[-1] == value[0]:
        value = value[1:-1]
    return value


def parse_db(url: str) -> dict:
    parsed = urlparse(url)
    db = parsed.path.lstrip('/')
    if not parsed.hostname or not parsed.username or not db:
        raise SystemExit(f'Malformed DATABASE_URL: {url}')
    return {
        'host': parsed.hostname,
        'port': parsed.port or 3306,
        'user': unquote(parsed.username or ''),
        'password': unquote(parsed.password or ''),
        'db': db,
    }


def write_cnf(path: str, creds: dict) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write('[client]\n')
        f.write(f"user={creds['user']}\n")
        f.write(f"password={creds['password']}\n")
        f.write(f"host={creds['host']}\n")
        f.write(f"port={creds['port']}\n")
    os.chmod(path, 0o600)


def mysql_query(cnf: str, db: str | None, sql: str) -> list[str]:
    cmd = ['mysql', f'--defaults-extra-file={cnf}', '-Nse', sql]
    if db:
        cmd.insert(2, db)
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    output = result.stdout.strip()
    return [] if not output else output.splitlines()


main = parse_db(load_db_url(main_env))
dev = parse_db(load_db_url(dev_env))

print(f"source_db={main['db']}@{main['host']}")
print(f"target_db={dev['db']}@{dev['host']}")

if main['db'] == dev['db'] and main['host'] == dev['host'] and main['port'] == dev['port'] and main['user'] == dev['user']:
    raise SystemExit('Source and target database connections are identical; aborting.')

main_cnf = os.path.join(backup_dir, 'main.cnf')
dev_cnf = os.path.join(backup_dir, 'dev.cnf')
write_cnf(main_cnf, main)
write_cnf(dev_cnf, dev)

backup_dump = os.path.join(backup_dir, f"{dev['db']}.sql.gz")
main_dump = os.path.join(backup_dir, f"{main['db']}.sql")

subprocess.run(
    f"mysqldump --no-tablespaces --defaults-extra-file={shlex.quote(dev_cnf)} --single-transaction --routines --triggers --events --add-drop-table {shlex.quote(dev['db'])} | gzip -9 > {shlex.quote(backup_dump)}",
    shell=True,
    check=True,
)
subprocess.run(
    f"mysqldump --no-tablespaces --defaults-extra-file={shlex.quote(main_cnf)} --single-transaction --routines --triggers --events --add-drop-table {shlex.quote(main['db'])} > {shlex.quote(main_dump)}",
    shell=True,
    check=True,
)

schema_info = mysql_query(
    main_cnf,
    None,
    f"SELECT default_character_set_name, default_collation_name FROM information_schema.schemata WHERE schema_name='{main['db']}'",
)
if not schema_info:
    raise SystemExit(f'Could not read schema metadata for {main["db"]}')
charset, collate = schema_info[0].split('\t')

subprocess.run(
    [
        'mysql',
        f'--defaults-extra-file={dev_cnf}',
        '-e',
        f"DROP DATABASE IF EXISTS `{dev['db']}`; CREATE DATABASE `{dev['db']}` CHARACTER SET {charset} COLLATE {collate};",
    ],
    check=True,
)
subprocess.run(
    f"mysql --defaults-extra-file={dev_cnf} {shlex.quote(dev['db'])} < {shlex.quote(main_dump)}",
    shell=True,
    check=True,
)

main_tables = mysql_query(
    main_cnf,
    main['db'],
    f"SELECT table_name FROM information_schema.tables WHERE table_schema='{main['db']}' ORDER BY table_name;",
)
dev_tables = mysql_query(
    dev_cnf,
    dev['db'],
    f"SELECT table_name FROM information_schema.tables WHERE table_schema='{dev['db']}' ORDER BY table_name;",
)
if main_tables != dev_tables:
    raise SystemExit('Table list mismatch after sync')

main_ver = mysql_query(main_cnf, main['db'], 'SELECT version_num FROM alembic_version LIMIT 1;')
dev_ver = mysql_query(dev_cnf, dev['db'], 'SELECT version_num FROM alembic_version LIMIT 1;')
if main_ver != dev_ver:
    raise SystemExit('Alembic version mismatch after sync')

print(f"backup_dev_dump={backup_dump}")
print(f"synced_tables={len(main_tables)}")
print(f"alembic_version={main_ver[0] if main_ver else 'missing'}")
PY
'''

cmd = [
    "ssh",
    "-4",
    "-o",
    "AddressFamily=inet",
    "-o",
    "StrictHostKeyChecking=yes",
    f"{PA_USERNAME}@{PA_HOST}",
    "bash -s",
]

print(f"Connecting to {PA_HOST} as {PA_USERNAME}...", file=sys.stderr)
subprocess.run(cmd, input=remote_script, text=True, check=True)

#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_ROOT"
DEPLOY_HOST=${DEPLOY_HOST:-aliyun}
TEMP_DEPLOY=$(mktemp -d)
trap 'rm -rf "$TEMP_DEPLOY"' EXIT
uv run --frozen ruff check backend/self_agent scripts/configure.py tests
uv run --frozen pytest -q
npm ci --prefix frontend --silent
npm run build --prefix frontend
if [ ! -f .env ]; then uv run --frozen python scripts/configure.py; fi
# Values remain in a private temporary file and never appear in shell arguments or logs.
uv run --frozen python - "$TEMP_DEPLOY/server.env" <<'PY'
import os,sys
from pathlib import Path
from dotenv import dotenv_values
values=dict(dotenv_values('.env'))
values.update(SECURE_COOKIE='true',APP_BASE_PATH='/self-agent',
              JEV_ENV_FILE='/opt/self-agent/secrets/jev.env',DATABASE_PATH='/opt/self-agent/data/self-agent.sqlite3')
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
 for k,v in values.items(): f.write(k+'='+str(v)+'\n')
PY
COPYFILE_DISABLE=1 tar --no-xattrs --exclude=__pycache__ -czf "$TEMP_DEPLOY/app.tar.gz" backend frontend/dist scripts pyproject.toml uv.lock README.md docs/design.md
ssh "$DEPLOY_HOST" 'mkdir -p /opt/self-agent /opt/self-agent/secrets; chmod 700 /opt/self-agent/secrets'
scp -q "$TEMP_DEPLOY/app.tar.gz" "$DEPLOY_HOST:/opt/self-agent/app.tar.gz"
scp -q "$TEMP_DEPLOY/server.env" "$DEPLOY_HOST:/opt/self-agent/.env"
if [ -f data/harmony-release.json ]; then
  ssh "$DEPLOY_HOST" 'mkdir -p /opt/self-agent/data'
  scp -q data/harmony-release.json "$DEPLOY_HOST:/opt/self-agent/data/harmony-release.json"
fi
scp -q "$HOME/.config/jev/.env" "$DEPLOY_HOST:/opt/self-agent/secrets/jev.env"
ssh "$DEPLOY_HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/self-agent
tar -xzf app.tar.gz
rm app.tar.gz
if ! id self-agent >/dev/null 2>&1; then useradd --system --home-dir /opt/self-agent --shell /sbin/nologin self-agent; fi
env -u VIRTUAL_ENV /opt/aiext/bin/uv sync --frozen --no-dev --python /opt/aiext/bin/python3 --default-index https://pypi.org/simple
mkdir -p data
chown -R self-agent:self-agent /opt/self-agent
chmod 600 .env secrets/jev.env
chmod 700 secrets data
install -m 644 scripts/self-agent.service /etc/systemd/system/self-agent.service
mkdir -p /etc/nginx/snippets
install -m 644 scripts/nginx-location.conf /etc/nginx/snippets/self-agent-location.conf
NGINX_CONF=/etc/nginx/conf.d/simple-agent.conf
BACKUP_CONF="$NGINX_CONF.bak-self-agent-$(date +%Y%m%d%H%M%S)"
cp "$NGINX_CONF" "$BACKUP_CONF"
NGINX_MAIN=/etc/nginx/nginx.conf
BACKUP_MAIN="$NGINX_MAIN.bak-self-agent-$(date +%Y%m%d%H%M%S)"
cp "$NGINX_MAIN" "$BACKUP_MAIN"
/opt/aiext/bin/python3 - <<'PY'
from pathlib import Path
p=Path('/etc/nginx/conf.d/simple-agent.conf')
s=p.read_text()
line='    include /etc/nginx/snippets/self-agent-location.conf;\n\n'
if line not in s:
 marker='    location / {'
 if s.count(marker)!=1: raise SystemExit('Cannot uniquely locate the existing nginx root location')
 p.write_text(s.replace(marker,line+marker))
# The mobile AppID already allows the apex domain. Reuse its existing certificate,
# explicitly bind SNI, and attach only this app's path to the existing HTTPS server.
p=Path('/etc/nginx/nginx.conf')
s=p.read_text()
cert='        ssl_certificate "/etc/letsencrypt/live/example.com/fullchain.pem";'
if Path('/etc/letsencrypt/live/example.com/fullchain.pem').exists():
 if cert not in s: raise SystemExit('Cannot uniquely locate mobile domain TLS server')
 before,after=s.split(cert,1)
 server_start=before.rfind('    server {')
 section=before[server_start:]
 if 'server_name  _;' in section:
  section=section.replace('server_name  _;', 'server_name  _ example.com;')
 elif 'example.com' not in section:
  raise SystemExit('Unexpected mobile domain server_name')
 if line.strip() not in section:
  section+=line
 s=before[:server_start]+section+cert+after
 p.write_text(s)
PY
if ! nginx -t; then cp "$BACKUP_CONF" "$NGINX_CONF"; cp "$BACKUP_MAIN" "$NGINX_MAIN"; exit 1; fi
systemctl daemon-reload
systemctl enable --now self-agent
systemctl restart self-agent
for attempt in $(seq 1 30); do
 if curl -fsS http://127.0.0.1:8025/api/health >/dev/null; then break; fi
 sleep 1
done
curl -fsS http://127.0.0.1:8025/api/health
systemctl reload nginx
systemctl is-active self-agent
REMOTE
curl -fsS https://example.com/self-agent/api/health
printf '\nDEPLOY_OK https://example.com/self-agent/\n'

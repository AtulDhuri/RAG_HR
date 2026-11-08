#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="${1:-/opt/knowledge-base}"
SERVICE_USER="knowledgebase"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this installer as root or with sudo."
  exit 1
fi

id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home "$PROJECT_ROOT" --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$PROJECT_ROOT/data/uploads" "$PROJECT_ROOT/logs"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$PROJECT_ROOT"
python3 -m venv "$PROJECT_ROOT/.venv"
"$PROJECT_ROOT/.venv/bin/python" -m pip install --upgrade pip
"$PROJECT_ROOT/.venv/bin/python" -m pip install -e "$PROJECT_ROOT[dev]"
"$PROJECT_ROOT/.venv/bin/python" -m alembic upgrade head

sed "s|__PROJECT_ROOT__|$PROJECT_ROOT|g; s|__SERVICE_USER__|$SERVICE_USER|g" "$PROJECT_ROOT/deployment/linux/knowledgebase-api.service" > /etc/systemd/system/knowledgebase-api.service
systemctl daemon-reload
systemctl enable --now knowledgebase-api.service
printf 'User Search: http://127.0.0.1:8000/user\nAdmin: http://127.0.0.1:8000/admin\n'

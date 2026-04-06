#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-trading-bot.service}"
BOT_USER="${BOT_USER:-${SUDO_USER:-${USER}}}"
BOT_GROUP="${BOT_GROUP:-${BOT_USER}}"
lookup_home() {
  local user="$1"
  if command -v getent >/dev/null 2>&1; then
    getent passwd "${user}" | cut -d: -f6
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    TARGET_USER="${user}" python3 - <<'PY'
import os
import pwd
print(pwd.getpwnam(os.environ["TARGET_USER"]).pw_dir)
PY
    return
  fi
  echo ""
}

BOT_HOME="${BOT_HOME:-$(lookup_home "${BOT_USER}")}"
PRINT_ONLY=0

if [[ "${1:-}" == "--print-only" ]]; then
  PRINT_ONLY=1
fi

if [[ -z "${BOT_HOME}" ]]; then
  echo "Fant ikke home-katalog for bruker ${BOT_USER}." >&2
  exit 1
fi

render_unit() {
  cat <<EOF
[Unit]
Description=Trading Bot Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${BOT_USER}
Group=${BOT_GROUP}
WorkingDirectory=${ROOT}
Environment=HOME=${BOT_HOME}
Environment=PATH=${BOT_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
ExecStart=${ROOT}/deploy/server/run-service.sh
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
}

if [[ "${PRINT_ONLY}" -eq 1 ]]; then
  render_unit
  exit 0
fi

if [[ "${EUID}" -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
  echo "Kjør med root eller installer sudo." >&2
  exit 1
fi

TMP_FILE="$(mktemp)"
trap 'rm -f "${TMP_FILE}"' EXIT
render_unit > "${TMP_FILE}"

if [[ "${EUID}" -eq 0 ]]; then
  install -m 0644 "${TMP_FILE}" "/etc/systemd/system/${SERVICE_NAME}"
  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}"
else
  sudo install -m 0644 "${TMP_FILE}" "/etc/systemd/system/${SERVICE_NAME}"
  sudo systemctl daemon-reload
  sudo systemctl enable --now "${SERVICE_NAME}"
fi

cat <<EOF
Service installert: ${SERVICE_NAME}

Nyttige kommandoer:
- sudo systemctl status ${SERVICE_NAME}
- sudo systemctl restart ${SERVICE_NAME}
- sudo journalctl -u ${SERVICE_NAME} -n 100 --no-pager
EOF

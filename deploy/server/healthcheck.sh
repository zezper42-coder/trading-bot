#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-trading-bot.service}"
TAIL_LINES="${TAIL_LINES:-30}"
SUDO=""
if [[ "${EUID}" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

echo "== systemctl =="
${SUDO} systemctl status "${SERVICE_NAME}" --no-pager
echo
echo "== recent logs =="
${SUDO} journalctl -u "${SERVICE_NAME}" -n "${TAIL_LINES}" --no-pager

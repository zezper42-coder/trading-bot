#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-trading-bot.service}"
SUDO=""
if [[ "${EUID}" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

export PATH="${HOME}/.local/bin:${PATH}"

cd "${ROOT}"

if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git pull --ff-only
fi

uv sync --no-editable

${SUDO} systemctl restart "${SERVICE_NAME}"

echo "Oppdatert og restartet ${SERVICE_NAME}."

#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUDO=""
if [[ "${EUID}" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

install_packages_apt() {
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y \
    build-essential \
    ca-certificates \
    curl \
    git \
    python3 \
    python3-venv \
    sqlite3
}

install_packages_dnf() {
  ${SUDO} dnf install -y \
    ca-certificates \
    curl \
    gcc \
    gcc-c++ \
    git \
    make \
    python3 \
    python3-pip \
    sqlite
}

if command -v apt-get >/dev/null 2>&1; then
  install_packages_apt
elif command -v dnf >/dev/null 2>&1; then
  install_packages_dnf
else
  echo "Fant verken apt-get eller dnf. Installer Python 3.12+, git, curl og sqlite3 manuelt." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

export PATH="${HOME}/.local/bin:${PATH}"

cd "${ROOT}"
mkdir -p logs data

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Opprettet .env fra .env.example. Fyll inn nøklene før du starter service." >&2
fi

uv sync --no-editable

cat <<EOF
Bootstrap ferdig.

Neste steg:
1. Rediger ${ROOT}/.env
2. Installer systemd-service:
   sudo ${ROOT}/deploy/server/install-systemd.sh
3. Sjekk status:
   ${ROOT}/deploy/server/healthcheck.sh
EOF

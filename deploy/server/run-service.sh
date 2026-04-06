#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

export PATH="${HOME}/.local/bin:${PATH}"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1

mkdir -p logs data

COMMAND="${BOT_COMMAND:-run-paper}"
LOG_LEVEL="${BOT_LOG_LEVEL:-INFO}"

case "${COMMAND}" in
  run-paper|run-once|run-earnings|scan-earnings)
    ;;
  *)
    echo "Ugyldig BOT_COMMAND: ${COMMAND}" >&2
    echo "Gyldige verdier: run-paper, run-once, run-earnings, scan-earnings" >&2
    exit 1
    ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  echo "uv ble ikke funnet i PATH. Kjør deploy/server/bootstrap.sh først." >&2
  exit 1
fi

exec uv run --no-editable python -m trading_bot.cli --log-level "${LOG_LEVEL}" "${COMMAND}"

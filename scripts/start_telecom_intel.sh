#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Safely parse local .env key=value pairs without shell execution (`source`/`eval`).
# Preserves caller-exported environment variables so runtime overrides always win.
load_dotenv_safely() {
  local env_file="$1"
  [ -f "$env_file" ] || return 0
  local line key val
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    [ -z "$line" ] && continue
    [[ "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || continue

    key="${line%%=*}"
    val="${line#*=}"

    case "$key" in
      export[[:space:]]*) key="${key#export}" ;;
    esac
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"

    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      continue
    fi

    # Respect already-set caller environment variables
    if [ -n "${!key+x}" ]; then
      continue
    fi

    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"

    if [ "${#val}" -ge 2 ]; then
      if [[ "$val" == \"*\" ]]; then
        val="${val#\"}"
        val="${val%\"}"
      elif [[ "$val" == \'*\' ]]; then
        val="${val#\'}"
        val="${val%\'}"
      fi
    fi

    export "${key}=${val}"
  done < "$env_file"
}

load_dotenv_safely ".env"

# Prefer local .venv if present, otherwise fall back to active Python/Streamlit environment
if [ -x ".venv/bin/python" ] && [ -x ".venv/bin/streamlit" ]; then
  PYTHON_BIN=".venv/bin/python"
  STREAMLIT_BIN=".venv/bin/streamlit"
else
  PYTHON_BIN="$(command -v python3 || true)"
  STREAMLIT_BIN="$(command -v streamlit || true)"
  if [ -z "$PYTHON_BIN" ]; then
    echo "Error: Python 3 executable not found (.venv/bin/python or python3 on PATH)." >&2
    echo "Create the environment first: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
    exit 1
  fi
  if [ -z "$STREAMLIT_BIN" ]; then
    echo "Error: Streamlit executable not found (.venv/bin/streamlit or streamlit on PATH)." >&2
    echo "Install project dependencies first: .venv/bin/pip install -e '.[dev]'" >&2
    exit 1
  fi
fi

validate_port() {
  local var_name="$1"
  local raw_val="$2"
  if [[ ! "$raw_val" =~ ^[0-9]+$ ]]; then
    echo "Error: Invalid ${var_name}='${raw_val}'. Expected a numeric TCP port between 1 and 65535." >&2
    exit 1
  fi
  local port_num=$((10#$raw_val))
  if [ "$port_num" -lt 1 ] || [ "$port_num" -gt 65535 ]; then
    echo "Error: Out-of-range ${var_name}='${raw_val}'. Port must be between 1 and 65535." >&2
    exit 1
  fi
}

API_PORT="${TELECOM_API_PORT:-8000}"
UI_PORT="${STREAMLIT_SERVER_PORT:-8501}"
validate_port "TELECOM_API_PORT" "$API_PORT"
validate_port "STREAMLIT_SERVER_PORT" "$UI_PORT"
API_PORT="$((10#$API_PORT))"
UI_PORT="$((10#$UI_PORT))"
export TELECOM_API_PORT="$API_PORT"
export STREAMLIT_SERVER_PORT="$UI_PORT"

echo "Starting Bangladesh Telecom Intelligence API (http://127.0.0.1:${API_PORT})..."
PYTHONPATH=. "$PYTHON_BIN" -m bd_telecom_scraper.api.server &
API_PID=$!

cleanup() {
  echo "Stopping local API server (PID $API_PID)..."
  kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for API readiness before starting Streamlit (avoids blind sleep race)
API_READY=0
for _ in $(seq 1 40); do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "Error: Local API server (PID $API_PID) exited unexpectedly before becoming healthy." >&2
    exit 1
  fi
  if "$PYTHON_BIN" -c 'import os, urllib.request; port = int(os.environ["TELECOM_API_PORT"]); urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)' >/dev/null 2>&1; then
    API_READY=1
    break
  fi
  sleep 0.25
done

if [ "$API_READY" -ne 1 ]; then
  echo "Error: Local API server did not become ready on http://127.0.0.1:${API_PORT}/api/health." >&2
  exit 1
fi

echo "Launching Editorial Dashboard (http://127.0.0.1:${UI_PORT})..."
PYTHONPATH=. "$STREAMLIT_BIN" run bd_telecom_scraper/dashboard/app.py --server.address 127.0.0.1 --server.port "$UI_PORT"

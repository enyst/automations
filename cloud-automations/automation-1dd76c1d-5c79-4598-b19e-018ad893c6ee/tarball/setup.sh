#!/bin/bash
# Install the OpenHands SDK from PyPI into an isolated virtual environment.
#
# The SDK version is fetched from the automation service API on every run so
# that deploying a new service version is the only step required to roll out a
# new SDK — no tarball re-generation or hardcoded version pins needed.
set -e

echo "[setup] Fetching SDK version from automation service"
PYTHON_JSON=python3
if ! command -v python3 >/dev/null 2>&1; then
    if command -v python >/dev/null 2>&1; then
        PYTHON_JSON=python
    elif command -v py >/dev/null 2>&1; then
        PYTHON_JSON='py -3'
    else
        echo "[setup] ERROR: python3, python, or py is required to parse SDK version" >&2
        exit 1
    fi
fi
set +e
SDK_VERSION=$(curl -sf "${AUTOMATION_API_URL}/sdk-version" \
  | ${PYTHON_JSON} -c "import sys, json; print(json.load(sys.stdin)['version'])" 2>/dev/null)
set -e
if [ -z "$SDK_VERSION" ]; then
    echo "[setup] ERROR: Failed to fetch SDK version from ${AUTOMATION_API_URL}/sdk-version" >&2
    exit 1
fi

# Best-effort progress phase for the dashboard — must never fail the setup.
PHASE_TOKEN="${AUTOMATION_CALLBACK_API_KEY:-${OPENHANDS_API_KEY:-}}"
if [ -n "${AUTOMATION_PHASE_URL:-}" ] && [ -n "$PHASE_TOKEN" ]; then
    curl -sf -m 5 -X POST "$AUTOMATION_PHASE_URL" \
      -H "Authorization: Bearer $PHASE_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"phase": "Installing dependencies"}' >/dev/null 2>&1 || true
fi

echo "[setup] Creating isolated virtual environment"
uv venv .venv --python '>=3.12' --quiet

echo "[setup] Installing OpenHands SDK from PyPI (version: $SDK_VERSION)"
uv pip install --quiet \
  "openhands-sdk==${SDK_VERSION}" \
  "openhands-tools==${SDK_VERSION}" \
  "openhands-workspace==${SDK_VERSION}"

echo "[setup] Done"

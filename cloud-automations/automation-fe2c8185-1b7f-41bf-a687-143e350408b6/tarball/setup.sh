#!/bin/bash
# Setup for the attention-router automation.
#
# Installs the OpenHands SDK into an isolated .venv (matching the service's SDK
# version), because the cloud scorer imports openhands.workspace / openhands.sdk
# to reach the account's LLM profile and secrets. The entrypoint runs with
# `.venv/bin/python run.py`, so the SDK must live in that venv. Mirrors the
# service's own preset setup.sh.
set -e

PYTHON_JSON=python3
if ! command -v python3 >/dev/null 2>&1; then
  if command -v python >/dev/null 2>&1; then PYTHON_JSON=python; fi
fi

echo "[setup] Fetching SDK version from ${AUTOMATION_API_URL}/sdk-version"
set +e
SDK_VERSION=$(curl -sf "${AUTOMATION_API_URL}/sdk-version" \
  | ${PYTHON_JSON} -c "import sys, json; print(json.load(sys.stdin)['version'])" 2>/dev/null)
set -e
if [ -z "$SDK_VERSION" ]; then
  echo "[setup] ERROR: could not fetch SDK version from ${AUTOMATION_API_URL}/sdk-version" >&2
  exit 1
fi

echo "[setup] Creating isolated venv and installing OpenHands SDK ${SDK_VERSION}"
uv venv .venv --python '>=3.12' --quiet
uv pip install --quiet \
  "openhands-sdk==${SDK_VERSION}" \
  "openhands-tools==${SDK_VERSION}" \
  "openhands-workspace==${SDK_VERSION}"

echo "[setup] Done"

#!/bin/sh
# SDK conversation and Cloud profile adapter; no terminal/browser tool package.
set -eu
uv venv .venv --python '>=3.12' --quiet
uv pip install --python .venv/bin/python --quiet \
    'openhands-sdk==1.49.2' \
    'openhands-workspace==1.49.2'

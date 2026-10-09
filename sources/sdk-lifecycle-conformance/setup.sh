#!/bin/sh
set -eu
python3 -m venv .verifier-venv
.verifier-venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt

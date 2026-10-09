#!/bin/sh
set -eu
sh setup_experimental.sh
exec .verifier-venv/bin/python self_contained.py

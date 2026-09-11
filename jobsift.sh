#!/bin/sh
# jobsift, run with this folder's own Python environment, from anywhere:
#   ./jobsift.sh --setup     ./jobsift.sh --once     ./jobsift.sh --export jobs.csv
# It runs inside this folder, because config.yaml, .env and data/ live here.
cd "$(dirname "$0")" && exec ./.venv/bin/python -m jobsift "$@"

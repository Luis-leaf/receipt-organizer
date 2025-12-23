#!/usr/bin/env bash
set -e

PROJECT_DIR="$HOME/project dir"
PYTHON="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR"

echo "Exec organizer..."
"$PYTHON" main.py

echo "Finish"

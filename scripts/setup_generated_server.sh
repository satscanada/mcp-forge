#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-.}"

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Error: target directory not found: $TARGET_DIR" >&2
  exit 1
fi

if [[ ! -f "$TARGET_DIR/requirements.txt" ]]; then
  echo "Error: requirements.txt not found in $TARGET_DIR" >&2
  exit 1
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  CANDIDATES=("$PYTHON_BIN")
else
  CANDIDATES=(python3.12 python3.11 python3.10 python3)
fi

PYTHON_BIN=""
for candidate in "${CANDIDATES[@]}"; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
    then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Error: no Python 3.10+ interpreter found. Set PYTHON_BIN to a compatible Python." >&2
  exit 1
fi

echo "Setting up generated server in $TARGET_DIR"
"$PYTHON_BIN" --version

if [[ -d "$TARGET_DIR/.venv" ]]; then
  OLD_VENV="$TARGET_DIR/.venv.old.$(date +%s)"
  echo "Rotating existing virtual environment to $OLD_VENV"
  mv "$TARGET_DIR/.venv" "$OLD_VENV"
  rm -rf "$OLD_VENV" >/dev/null 2>&1 || true
fi

"$PYTHON_BIN" -m venv "$TARGET_DIR/.venv"
"$TARGET_DIR/.venv/bin/python" -m pip install --upgrade pip
"$TARGET_DIR/.venv/bin/python" -m pip install -r "$TARGET_DIR/requirements.txt"

cat <<EOF

Setup complete.

Next steps:
  cd $TARGET_DIR
  source .venv/bin/activate
  edit .env
  python server.py
EOF

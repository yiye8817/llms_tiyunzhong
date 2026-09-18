#!/usr/bin/env bash
# Build a local diagnostic archive; never uploads or requires root.
set -euo pipefail
TASK_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${FUSION_PYTHON:-}" ]]; then
  TASK_PYTHON="$FUSION_PYTHON"
elif [[ -x "$TASK_ROOT/.venv/bin/python" ]]; then
  TASK_PYTHON="$TASK_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  TASK_PYTHON="$(command -v python3)"
else
  echo '需要 Python 3；打包仅使用标准库，不安装依赖。' >&2
  exit 1
fi
exec "$TASK_PYTHON" "$TASK_ROOT/scripts/package_diagnostics.py" "$@"

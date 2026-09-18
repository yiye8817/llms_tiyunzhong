#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
SKIP_INSTALL=0
INSTALL_ONLY=0
CHECK_ONLY=0
BACKEND_ONLY=0
ELECTRON_ARGS=()
while (($#)); do
  case "$1" in
    --skip-install) SKIP_INSTALL=1 ;;
    --install-only) INSTALL_ONLY=1 ;;
    --check) CHECK_ONLY=1 ;;
    --backend) BACKEND_ONLY=1 ;;
    --no-sandbox|--disable-setuid-sandbox) ELECTRON_ARGS+=("$1") ;;
    -h|--help)
      cat <<'HELP'
MultiLLM Fusion — Python + Electron Linux 桌面应用
  ./run.sh                 创建隔离 Python 环境、安装依赖并启动
  ./run.sh --skip-install  使用已安装依赖启动
  ./run.sh --install-only  只安装依赖与 Electron 二进制
  ./run.sh --check         安装依赖并运行本地测试，不启动桌面
  ./run.sh --backend       前台启动 Python 服务以查看诊断信息
  ./run.sh --no-sandbox    无 root 兼容启动：关闭 Chromium 进程沙箱，仅供临时排障
  ./run.sh -- --ozone-platform=x11   将参数传给 Electron

环境：Python >= 3.10，Node >= 22.12，npm，Linux 64 位桌面。
可选变量：FUSION_DATA_DIR、FUSION_PORT（默认 8765）、FUSION_PYTHON。
关键运行日志同步输出到终端和项目 logs/；FUSION_LOG_DIR 可指定日志目录。
复现后运行 ./package.sh，将代码和脱敏日志打包用于分析；不会自动上传。
下载代理沿用 HTTPS_PROXY/HTTP_PROXY；Electron 镜像可设置 ELECTRON_MIRROR。
Linux 默认禁用 SUID 沙箱并尝试用户命名空间沙箱，无需 root 或修改 chrome-sandbox 权限。
如果系统禁止用户命名空间，程序会报错退出；不会自动关闭全部沙箱。
HELP
      exit 0 ;;
    --) shift; ELECTRON_ARGS+=("$@"); break ;;
    *) echo "未知参数：$1；请使用 --help" >&2; exit 2 ;;
  esac
  shift
done
command -v python3 >/dev/null || { echo '请先安装 Python 3.10+ 和 venv。' >&2; exit 1; }
command -v node >/dev/null || { echo '请先安装 Node.js 22.12+。' >&2; exit 1; }
command -v npm >/dev/null || { echo '请先安装 npm。' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22 || (a===22 && b<12)){console.error("需要 Node.js 22.12+");process.exit(1)}'
if [[ "$SKIP_INSTALL" == 0 ]]; then
  if [[ -z "${FUSION_PYTHON:-}" ]]; then
    if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
      python3 -m venv "$ROOT_DIR/.venv" || { echo '创建 venv 失败；Ubuntu/Debian 请安装 python3-venv。' >&2; exit 1; }
    fi
    export FUSION_PYTHON="$ROOT_DIR/.venv/bin/python"
  fi
  "$FUSION_PYTHON" -m pip install -r requirements.txt
  if ! "$FUSION_PYTHON" -m pip install -r requirements-browser.txt; then
    echo '浏览器解密组件安装失败；Firefox/未加密 Cookie 读取和扩展登录文件导入仍可使用。' >&2
    echo '稍后可运行 .venv/bin/python -m pip install -r requirements-browser.txt 重试。' >&2
  fi
  if [[ ! -d node_modules || package-lock.json -nt node_modules/.package-lock.json ]]; then
    npm ci --no-audit --no-fund
  fi
  # Electron 42+ downloads via this explicit installer, no longer postinstall.
  node node_modules/electron/install.js
else
  export FUSION_PYTHON="${FUSION_PYTHON:-$ROOT_DIR/.venv/bin/python}"
fi
[[ -x "$FUSION_PYTHON" ]] || { echo "Python 环境不存在：$FUSION_PYTHON；运行 ./run.sh 安装。" >&2; exit 1; }
[[ -f node_modules/.bin/electron ]] || { echo 'Electron 依赖不存在；运行 ./run.sh 安装。' >&2; exit 1; }
if [[ "$INSTALL_ONLY" == 1 ]]; then echo '启动依赖准备完成；可选浏览器解密组件状态见上方输出。运行 ./run.sh --skip-install。'; exit 0; fi
if [[ "$CHECK_ONLY" == 1 ]]; then
  "$FUSION_PYTHON" -m unittest discover -s tests -p 'test_*.py' -v
  npm test
  npm run check
  exit 0
fi
if [[ "$BACKEND_ONLY" == 1 ]]; then
  exec "$FUSION_PYTHON" -m uvicorn backend.app:app --host 127.0.0.1 --port "${FUSION_PORT:-8765}"
fi
if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo '未检测到 Linux 图形桌面 DISPLAY/WAYLAND_DISPLAY；请在桌面终端运行。' >&2
  exit 1
fi
if [[ "$(id -u)" == 0 ]]; then
  echo '请用普通桌面用户启动应用；不要用 sudo 运行浏览器。' >&2
  exit 1
fi
exec node scripts/launch-electron.cjs "${ELECTRON_ARGS[@]}"

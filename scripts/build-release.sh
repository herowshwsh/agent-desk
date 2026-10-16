#!/bin/sh
# 一键出包：Python 桥接（PyInstaller 单文件）→ Agent.app（electron-builder）。
# 前置：已创建 .venv（见 README「快速开始」）、已 npm install。
# 产物：release/mac-arm64/Agent.app
set -eu

APP_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$APP_ROOT"

PYTHON_BIN="${PYTHON_BIN:-$APP_ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "找不到 Python 虚拟环境：$PYTHON_BIN" >&2
  echo "请先创建：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-build.txt" >&2
  exit 1
fi

echo "[1/3] 打包 Python 桥接..."
"$PYTHON_BIN" -m PyInstaller --clean --noconfirm --onefile --console \
  --name agentdesk_bridge run_agentdesk_bridge.py
# electron-builder 的 extraResources 引用 dist/agentdesk_bridge ✓

echo "[2/3] 打包 Electron 应用..."
# 用系统原生 node 跑 electron-builder（部分终端/沙箱环境的 node 包装器会拦截
# asar 写入，导致打包失败或产出白屏应用，所以这里显式解析真实 node）。
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
if [ -z "$NODE_BIN" ]; then
  echo "找不到 node，请安装 Node.js 18+，或用 NODE_BIN=/path/to/node 指定。" >&2
  exit 1
fi
"$NODE_BIN" "$APP_ROOT/node_modules/electron-builder/cli.js" --mac --dir

echo "[3/3] 完成。"
APP_PATH="$(ls -d "$APP_ROOT"/release/mac*/Agent.app 2>/dev/null | head -1)"
echo "产物：$APP_PATH"
echo ""
echo "分发给别人后，对方首次打开到「设置」里填写配置即可："
echo "  - 模型与密钥：设置 → 模型管理"
echo "  - 服务器账号：设置 → 服务器管理"
echo "用户配置目录：~/Library/Application Support/Agent/"
echo ""
echo "注意：本脚本打的是出厂模板配置（models.example.json / servers.example.json），"
echo "不含本机真实密钥；升级 app 不会覆盖用户已填写的配置。"

# 打包说明（macOS）

本文档说明如何从源码打出可分发的 `Agent.app`。日常使用只需 README 的「打包发布」一节；这里给出完整过程与排错细节。

## 一、环境准备（只需一次）

```bash
# 1) 确认工具链
python3 --version        # 需要 3.11+
node --version           # 需要 18+
xcode-select -p          # 需要 Xcode Command Line Tools

# 2) 安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-build.txt   # 运行 + 打包依赖
npm install                                                            # Electron 与 electron-builder
```

`requirements-build.txt` 只包含打包需要的 PyInstaller，运行应用本身不需要它。

## 二、一键打包

```bash
npm run build:app
```

脚本 `scripts/build-release.sh` 依次执行三个阶段：

### 阶段 1：Python 桥接打成单个可执行文件

```bash
.venv/bin/python -m PyInstaller --clean --noconfirm --onefile --console \
  --name agentdesk_bridge run_agentdesk_bridge.py
```

- 产物：`dist/agentdesk_bridge`（约 20–40 MB，内含 Python 运行时与 paramiko、openai 等依赖）
- `--onefile`：桌面端只需分发这一个文件，目标机器无需安装 Python
- 打包配置由 `agentdesk_bridge.spec` 记录

### 阶段 2：Electron 应用封装

```bash
node node_modules/electron-builder/cli.js --mac --dir
```

- `--dir`：只产出 `.app` 目录，不封 dmg（要 dmg 就把 `--dir` 换成 `--mac dmg`）
- 打包规则来自 `package.json` 的 `build` 字段：
  - `files`：`renderer/`、`electron/`、`assets/` 进 `app.asar`
  - `extraResources`：图标、`dist/agentdesk_bridge`、`modules.json`、`models.example.json`、`servers.example.json` 放到 `Contents/Resources/`
- **不会打包你本机的 `models.json` / `servers.json`**（含真实密钥），出厂只带 `.example` 模板

### 阶段 3：产物

```text
release/mac-arm64/Agent.app
```

## 三、产物验收

```bash
# 1) 启动检查
open release/mac-arm64/Agent.app
# 左上角出现窗口、设置页能打开即正常；首次启动会在用户目录初始化配置

# 2) 确认包内不含真实密钥
grep -r "sk-" release/mac-arm64/Agent.app/Contents/Resources/models.example.json || echo "包内无真实密钥 ✓"

# 3) 确认桥接进程存在
ls -lh release/mac-arm64/Agent.app/Contents/Resources/agentdesk_bridge

# 4) 确认用户配置初始化
ls ~/Library/Application\ Support/Agent/
```

## 四、运行机制（分发对象视角）

1. 用户打开 `Agent.app`
2. 主进程检查 `~/Library/Application Support/Agent/`：
   - 不存在 `models.json` → 从包内 `models.example.json` 复制
   - 不存在 `servers.json` → 从包内 `servers.example.json` 复制
   - **已存在则跳过**，所以升级 app 不会覆盖用户数据
3. 主进程拉起包内 `agentdesk_bridge`（Python 单文件），通过 JSON Lines 通信
4. 用户在「设置」里填模型密钥与服务器账号，保存即生效

## 五、分发

```bash
# 打包 zip
cd release/mac-arm64 && zip -r Agent.zip Agent.app
```

- 未做代码签名，对方首次打开需：**右键 → 打开 → 仍要打开**（macOS Gatekeeper 提示属正常）
- 有 Apple 开发者账号时可在 `package.json` 的 `build.mac` 中配置 `identity` 与 `notarize` 做签名公证

## 六、排错

| 现象 | 原因与处理 |
|---|---|
| 打包产物打开白屏 | electron-builder 被包装过的 node 调用，asar 写入被拦截。脚本已用 `command -v node` 解析真实 node；仍失败时用 `NODE_BIN=/usr/local/bin/node npm run build:app` 显式指定 |
| `PyInstaller: command not found` | 打包依赖没装：`.venv/bin/pip install -r requirements-build.txt` |
| 找不到 Python 虚拟环境 | 先执行「一、环境准备」，或用 `PYTHON_BIN=... npm run build:app` 指定 |
| Intel Mac 打包报架构不符 | 把 `package.json` 的 `mac.target` 改为 `{ "target": "dir", "arch": ["x64"] }` |
| 打包后改了代码但界面没变 | macOS 关窗口不退出进程，`Cmd + Q` 完全退出再打开 |
| 想让应用换名字/图标 | 图标替换 `assets/Agent.icns`；名字改 `package.json` 的 `productName`（注意：改 productName 会更换用户配置目录，老配置需手动迁移） |

## 七、开发调试与打包的区别

| | 开发模式（`npm run desktop`） | 打包版 |
|---|---|---|
| Python 运行 | 直接用 `.venv-mcp/bin/python` 跑源码 | 包内 `agentdesk_bridge` 单文件 |
| 配置目录 | 项目根目录 | `~/Library/Application Support/Agent/` |
| 改代码生效 | 重启应用即可 | 需重新打包 |
| 日志 | 终端直接可见 | 主进程 console（`Console.app` 里看 Agent 日志） |

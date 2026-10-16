# AgentDesk

一个本地运行的桌面 AI Agent 工作台：**模型负责理解意图，Python 负责安全校验与工具执行**，Electron 提供桌面界面。

所有配置（模型、密钥、服务器账号）都在应用内的「设置」页维护，不需要手改文件。

![平台](https://img.shields.io/badge/platform-macOS-blue)



![Python](https://img.shields.io/badge/python-3.11%2B-blue)

![Node](https://img.shields.io/badge/node-18%2B-green)

## 特性

**服务器助手（agent 模式）**

- 通过 SSH 查询主机名、系统、负载、CPU/内存/磁盘、内网地址
- 查询 systemd 服务与进程、PM2 进程列表
- PM2 启停重启：属于写操作，执行前弹出确认卡片，用户批准才执行
- 右侧实时显示执行轨迹（意图解析 → 安全检查 → 工具调用 → 结果）
- 支持配置多台服务器，在输入框右下角随时切换目标机器

**自由聊天（chat 模式）**

- 消息原样转发给模型，携带多轮上下文，不触碰服务器
- 独立的会话与模型选择，与服务器助手互不干扰

**模型管理（设置页）**

- 列表式管理：添加 / 编辑 / 删除 / 设默认，保存后立即生效，无需重启
- 支持 OpenAI 兼容的两种协议：`chat`（Chat Completions）与 `responses`（Responses API，如 GPT-5 系中转）
- 每个模型可单独设置推理强度（low/medium/high）、响应是否留存
- 密钥直接填在表单里，本地保存，随时更换

**服务器管理（设置页）**

- 多服务器列表，每台可「测试连接」——直接用当前表单值连一次 SSH，显示耗时或失败原因
- 密码留空即保留原值，不会被误清空

**写文件的两步确认**

- 模型要写文件时，只输出 `[写入提案]`（目标文件、理由、怎么改），**此时不生成代码**
- 用户在界面点「确认写入」后，模型才生成内容并落盘
- 落盘前自动备份原文件（`.bak`），全程写入审计日志

**模块化**

- 界面模块由 `modules.json` 驱动，新增模块不需要改界面代码

## 界面说明

| 区域     | 说明                   |
| ------ | -------------------- |
| 顶部 Tab | 模块切换（服务器助手 / 自由聊天）   |
| 左侧     | 模块自己的会话列表（各模块互相隔离）   |
| 中间     | 对话框；欢迎页有快捷提示词        |
| 右侧     | 执行轨迹（仅 agent 模式模块显示） |
| 输入框右下  | 模型选择器；服务器助手额外有服务器选择器 |
| 左下「设置」 | 主题、背景图、模型管理、服务器管理    |

## 快速开始

### 环境要求

- **macOS**（Apple Silicon 默认；Intel Mac 见「常见问题」）
- **Python 3.11+**（开发环境使用 3.13）
- **Node.js 18+**（开发环境使用 22）
- 一个 OpenAI 兼容的模型服务（如 DeepSeek 官方接口、自建中转站）
- 如果要使用服务器助手：一台可以通过 SSH 登录的 Linux 服务器

### 安装

```bash
git clone <仓库地址>
cd ai-agent

# 1) Python 侧（运行时 + 打包依赖）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-build.txt

# 2) Node 侧（Electron 桌面壳）
npm install
```

> 仓库里 `models.json` / `servers.json` 不入库（含密钥）。项目自带  
> `models.example.json` 与 `servers.example.json` 作为出厂模板，首次使用时  
> 复制成正式文件即可（下面第 1 步）。

### 首次运行

```bash
# 1) 生成本地配置（只在第一次需要）
cp models.example.json models.json
cp servers.example.json servers.json

# 2) 启动桌面应用
npm run desktop
```

启动后点击左下角「**设置**」：

1. **模型管理** → 编辑默认模型（或「添加模型」）：填入 `接口地址`、`模型名称`、`密钥值` → 「保存全部」
2. **服务器管理**（可选）→ 编辑或添加服务器：填 `服务器地址`、`端口`、`登录用户`、`密码` → 「测试连接」确认能连上 → 「保存全部」

保存后立即可用，回到对话界面发送「查询服务器信息」即可看到效果。

## 配置说明

### 配置文件位置

| 运行方式                    | 配置目录                                   |
| ----------------------- | -------------------------------------- |
| 开发模式（`npm run desktop`） | 项目根目录                                  |
| 打包后的 Agent.app          | `~/Library/Application Support/Agent/` |

设置页保存时写入的就是这两个位置，**升级应用不会覆盖已有配置**。

命令行调试也可以直接用环境变量覆盖：

```bash
AGENT_MODEL_CONFIG=/path/to/models.json \
AGENT_SERVERS_CONFIG=/path/to/servers.json \
AGENT_MODULES_CONFIG=/path/to/modules.json \
.venv/bin/python run_agentdesk_bridge.py
```

### models.json

```json
{
  "default": "deepseek-chat",
  "models": [
    {
      "id": "deepseek-chat",
      "label": "DeepSeek",
      "model": "deepseek-chat",
      "base_url": "https://api.deepseek.com",
      "api_key": "sk-...",
      "wire_api": "chat",
      "reasoning_effort": "",
      "disable_response_storage": false,
      "enabled": true
    }
  ]
}
```

| 字段                         | 必填 | 说明                                          |
| -------------------------- | -- | ------------------------------------------- |
| `id`                       | 是  | 唯一标识（保存时自动生成/去重）                            |
| `label`                    | 否  | 选择器里的显示名，留空用 id                             |
| `model`                    | 否  | 实际模型名称，如 `deepseek-chat`                    |
| `base_url`                 | 否  | OpenAI 兼容接口地址，如 `https://api.deepseek.com`  |
| `api_key`                  | 否  | 密钥，直接存在本文件（本地个人应用）                          |
| `wire_api`                 | 否  | `chat`（默认）或 `responses`                     |
| `reasoning_effort`         | 否  | 仅 responses：`low` / `medium` / `high`       |
| `disable_response_storage` | 否  | 仅 responses：`true` 时传 `store=false`，不留服务端副本 |
| `enabled`                  | 否  | 是否出现在模型选择器，默认 `true`                        |

`default` 指定默认模型 id。格式问题不会拦截保存，使用时会给出明确提示。

### servers.json

```json
{
  "default": "server-1",
  "servers": [
    {
      "id": "server-1",
      "label": "我的服务器",
      "host": "1.2.3.4",
      "port": 22,
      "username": "root",
      "password": "...",
      "connect_timeout": 10
    }
  ]
}
```

`default` 指定服务器助手默认连接的那台；界面上随时可切换。`password` 存本文件（本地个人应用），设置页里以密文输入框呈现、不回显。

### modules.json

界面模块目录，新增模块只改这个文件：

| 字段                                                               | 说明                             |
| ---------------------------------------------------------------- | ------------------------------ |
| `id`                                                             | 模块标识，唯一；前端用它隔离会话与模型选择          |
| `label` / `hint`                                                 | Tab 名称与悬停说明                    |
| `icon`                                                           | `server` / `chat` / `spark`    |
| `mode`                                                           | `agent`：走意图路由与工具；`chat`：只做模型转发 |
| `show_trace`                                                     | 是否显示右侧执行轨迹栏（默认按 mode 推导）       |
| `show_sessions`                                                  | 是否显示会话列表                       |
| `model_locked`                                                   | `true` 时该模块固定使用默认模型            |
| `allow_files`                                                    | `true` 时该模块可挂载工作空间（读写文件）       |
| `welcome_title` / `welcome_subtitle` / `placeholder` / `prompts` | 欢迎页文案                          |
| `system_prompt`                                                  | 仅 `chat` 模式：系统提示               |

## 安全设计

- **只读查询**：服务器查询只执行固定命令表（`hostname`、`uptime`、`df` 等），不接受模型生成的任意 shell 命令
- **写操作要授权**：PM2 启停重启前弹确认卡片；写文件走「提案 → 用户确认 → 才生成」的两步流程
- **全部留痕**：工具调用写审计日志（`audit.jsonl`），执行过程写轨迹（`trace.jsonl`），文件写入前自动 `.bak` 备份
- **密钥不外发**：密钥只存在本机配置里，不随代码或日志输出（设置页只回显脱敏后的片段）
- **不收集任何数据**：除了你自己配置的模型接口和 SSH 服务器，程序不向任何第三方发起请求

## 测试

```bash
# Python 侧：配置加载、工具校验、写入授权、模型/服务器管理（80 项）
.venv-mcp/bin/python -m unittest tests.test_modules

# 模块分发的离线端到端（stub 掉模型与 SSH，不发真实请求）
.venv-mcp/bin/python tests/e2e_modules.py

# 渲染层（不需要安装任何依赖）
node tests/renderer_static_check.mjs   # 结构/样式/残留检查
node tests/renderer_behavior.mjs       # 模块切换、会话隔离、交互行为
```

测试不连接真实模型、不修改任何远端数据。

## 打包发布

> 完整的环境准备、分步说明、产物验收与排错表见 **[docs/BUILD.md](docs/BUILD.md)**。

一键出包（macOS）：

```bash
npm run build:app          # 等价于 sh scripts/build-release.sh
```

脚本做三件事：

1. **PyInstaller** 把 Python 桥接打成单文件 `dist/agentdesk_bridge`（含 paramiko、openai 等依赖）
2. **electron-builder** 用系统原生 node 打出 `release/mac-arm64/Agent.app`（`--dir` 免签名直出目录）
3. 打印产物路径与分发提示

### 手工分步（等同脚本内容）

```bash
# 1) 桥接进程（单文件，自包含 Python 运行时）
.venv/bin/python -m PyInstaller --clean --noconfirm --onefile --console \
  --name agentdesk_bridge run_agentdesk_bridge.py

# 2) 桌面应用（输出到 release/，--dir 表示不封装 dmg）
node node_modules/electron-builder/cli.js --mac --dir
```

### 产物结构

```text
release/mac-arm64/Agent.app/
└── Contents/
    ├── MacOS/Agent                    # Electron 主进程
    └── Resources/
        ├── app.asar                   # 渲染层 + electron 代码
        ├── agentdesk_bridge           # PyInstaller 单文件桥接
        ├── modules.json               # 模块目录
        └── models.example.json        # 出厂模型模板（占位）
        └── servers.example.json       # 出厂服务器模板（占位）
```

包内**只带模板**，不含任何真实密钥。用户首次启动时，主进程把模板复制到  
`~/Library/Application Support/Agent/`（已存在则跳过），之后所有修改都在用户目录，升级不丢。

### 分发给别人

1. 把 `Agent.app` 打包成 zip 发出去（或上传 Release）
2. 对方解压后**首次打开**：macOS 会提示"无法验证开发者"→ 右键 →「打开」→「仍要打开」  
   （未做代码签名，属于正常现象；有 Apple 开发者账号可在 `package.json` 的 `mac` 里配置签名与公证）
3. 对方打开后到「设置 → 模型管理」填写自己的密钥，即可开始使用

### 打包注意事项

- **必须用真实 node 跑 electron-builder**。某些终端环境（如 IDE 自带的 node 包装器）会拦截 asar 写入，导致产物白屏 —— 脚本已自动解析 `command -v node`，也可用 `NODE_BIN=/path/to/node npm run build:app` 指定
- 换图标：替换 `assets/Agent.icns` 与 `assets/icon.png`，重新打包即可
- Intel Mac：把 `package.json` 的 `mac.target` 改为 `{ "target": "dir", "arch": ["x64"] }` 后打包
- 需要 `.dmg` 安装包：把脚本里的 `--dir` 换成 `--mac dmg`

## 目录结构

```text
agentdesk_core/            # Python 核心
├── agent.py               # 工作流编排（模型调用、工具循环、写文件两步授权）
├── server_client.py       # SSH 服务器工具（固定只读命令 + PM2 管理）
├── config.py              # 配置加载（模型 / 服务器 / 模块）
├── model_store.py         # 模型配置的读取、宽松校验、保存
├── server_store.py        # 服务器配置的读取、宽松校验、保存（密码不回显）
├── attachments.py         # 工作空间文件读写（备份 / diff / 文件树）
├── guardrails.py          # 白名单与参数校验
├── memory.py              # 多轮记忆
└── tracing.py             # 审计与轨迹
electron/
├── main.cjs               # 窗口、IPC、桥接进程管理、出厂配置初始化
└── preload.cjs            # 安全 IPC 通道
renderer/
├── index.html             # 工作台结构
├── styles.css             # 主题与样式（CSS 变量驱动）
└── app.js                 # 模块切换、会话、设置页、交互逻辑
run_agentdesk_bridge.py    # 桌面版入口（JSON Lines 桥接）
modules.json               # 模块目录（入库）
models.example.json        # 模型配置模板（入库）
servers.example.json       # 服务器配置模板（入库）
scripts/build-release.sh   # 一键出包
tests/                     # 测试
```

## 常见问题

**改了代码/更新了版本，界面没变化？**  
macOS 上点关闭按钮只是收起窗口，进程还在。请 **Cmd + Q 完全退出** 再重新打开。

**提示"还没有配置密钥"？**  
到「设置 → 模型管理」对应模型里填入密钥值并保存。

**服务器助手连不上？**  
到「设置 → 服务器管理」点「测试连接」，它会用当前表单值真实连一次并给出原因（认证失败 / 超时 / 配置不完整）。

**数据和配置在哪？**  
打包版：`~/Library/Application Support/Agent/`（`models.json`、`servers.json`、`modules.json`，以及运行时产生的 `agentdesk_audit.jsonl` / `agentdesk_trace.jsonl`）。

**能不用界面、只跑命令行吗？**  
可以，直接用桥接进程的 JSON Lines 协议交互：`.venv/bin/python run_agentdesk_bridge.py`，发送 `{"type":"chat","question":"查询服务器信息","module_id":"server"}`。

## 许可

本项目尚未指定开源许可证，仓库作者可自行补充（如 MIT / Apache-2.0）。

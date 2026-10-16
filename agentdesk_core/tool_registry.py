"""服务器模块的工具注册表。

新增能力时在这里注册一个 ToolSpec（名字、描述、参数 schema），模型就会通过
function calling 自动选择并传参 —— 不再需要改分类提示词和路由分支。
执行与权限仍走 guardrails（白名单、确认、审计）。
"""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ToolSpec:
    name: str        # 模型看到的工具名
    action: str      # 内部 action，对应 guardrails 权限表
    description: str
    parameters: Dict[str, Any]
    read_only: bool = True


PM2_PARAMETERS = {
    "type": "object",
    "properties": {
        "operation": {"type": "string", "enum": ["start", "stop", "restart"], "description": "要执行的操作"},
        "name": {"type": "string", "description": "PM2 进程名；用户没有明确给出时传空字符串"},
    },
    "required": ["operation", "name"],
}

SERVER_TOOLS = [
    ToolSpec(
        name="get_system_info",
        action="server_info",
        description="查询服务器系统信息：主机名、操作系统、CPU、内存、磁盘、运行时间。凡是询问服务器状态都用它。",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="get_services",
        action="service_info",
        description="查询服务器上正在运行的 systemd 服务列表。",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="get_pm2_processes",
        action="pm2_info",
        description="查询 PM2 管理的进程列表与运行状态。",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="manage_pm2_process",
        action="pm2_dynamic",
        description="启动、停止或重启一个 PM2 进程。这是敏感写操作，只有在用户明确要求时才调用。",
        parameters=PM2_PARAMETERS,
        read_only=False,
    ),
]

SERVER_TOOLS_BY_NAME = {spec.name: spec for spec in SERVER_TOOLS}

OPERATION_TO_ACTION = {"start": "pm2_start", "stop": "pm2_stop", "restart": "pm2_restart"}


def to_chat_tool_schemas() -> list[Dict[str, Any]]:
    """转成 chat completions 的 tools 格式；responses 的扁平转换由统一调用层处理。"""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in SERVER_TOOLS
    ]

"""服务器能力路由的轻量 RAG 检索。"""

import re
from typing import Any, Dict, List, Optional


ROUTING_KNOWLEDGE = [
    {
        "action": "pm2_restart",
        "keywords": ["重启", "pm2", "重新启动", "restart"],
        "example": "重启 PM2 的 web-app、重新启动 pm2 进程",
        "rule": "提取 PM2 进程名；没有进程名时保留 name=null，让系统追问。",
    },
    {
        "action": "pm2_start",
        "keywords": ["启动", "pm2", "start"],
        "example": "启动 PM2 的 web-app",
        "rule": "提取 PM2 进程名；没有进程名时保留 name=null，让系统追问。",
    },
    {
        "action": "pm2_stop",
        "keywords": ["停止", "关闭", "pm2", "stop"],
        "example": "停止 PM2 的 web-app",
        "rule": "提取 PM2 进程名；没有进程名时保留 name=null，让系统追问。",
    },
    {
        "action": "pm2_info",
        "keywords": ["pm2", "状态", "列表", "运行", "进程"],
        "example": "查询 PM2 当前运行的服务、查看 pm2 list",
        "rule": "查询 PM2 状态，不需要进程名。",
    },
    {
        "action": "service_info",
        "keywords": ["服务", "systemd", "进程", "运行"],
        "example": "查询服务器正在运行的服务",
        "rule": "查询服务器服务或进程，不需要修改服务器。",
    },
    {
        "action": "server_info",
        "keywords": ["服务器", "主机", "系统", "cpu", "内存", "磁盘", "运行时间"],
        "example": "查询我的服务器信息、查看 CPU 和内存",
        "rule": "查询服务器基础运行信息。",
    },
]


def retrieve_routing_context(question: str, limit: int = 4) -> List[Dict[str, str]]:
    """按关键词召回最相关的能力说明，供路由模型参考。"""
    text = question.lower()
    ranked = []
    for item in ROUTING_KNOWLEDGE:
        score = sum(1 for keyword in item["keywords"] if keyword.lower() in text)
        if score:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in ranked[:limit]]


def infer_pm2_plan(question: str) -> Optional[Dict[str, Any]]:
    """对明确的 PM2 指令做确定性兜底，避免模型把操作误判成普通问题。"""
    text = question.lower()
    if "pm2" not in text:
        return None

    if any(word in text for word in ("重启", "重新启动", "restart")):
        action = "pm2_restart"
    elif any(word in text for word in ("停止", "关闭", "stop")):
        action = "pm2_stop"
    elif any(word in text for word in ("启动", "start")):
        action = "pm2_start"
    elif any(word in text for word in ("查询", "查看", "状态", "列表", "运行", "list", "status")):
        return {"action": "pm2_info", "name": None}
    else:
        return None

    name = None
    patterns = (
        r"pm2\s*(?:的|中|进程|应用|服务)?\s*([a-z0-9][a-z0-9_.-]{0,79})",
        r"(?:进程|应用|服务)\s*(?:名为|叫|是)?\s*([a-z0-9][a-z0-9_.-]{0,79})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1)
            if candidate not in {"的", "中", "进程", "应用", "服务"}:
                name = candidate
                break
    return {"action": action, "name": name}

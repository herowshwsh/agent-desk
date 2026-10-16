"""模型计划的白名单、参数校验和权限分级。"""

from dataclasses import dataclass
import re
from typing import Any, Dict


PERMISSIONS = {
    "server_info": "READ_ONLY",
    "service_info": "READ_ONLY",
    "pm2_info": "READ_ONLY",
    "pm2_start": "REQUIRES_CONFIRMATION",
    "pm2_stop": "REQUIRES_CONFIRMATION",
    "pm2_restart": "REQUIRES_CONFIRMATION",
}
ALLOWED_ACTIONS = set(PERMISSIONS) | {"general", "unknown"}
ALLOWED_FIELDS = {"action", "name"}
WRITE_ACTIONS = {"pm2_start", "pm2_stop", "pm2_restart"}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    action: str
    permission: str
    plan: Dict[str, Any]
    reason: str = ""


def _blocked(action: str, reason: str, plan: Dict[str, Any]) -> Decision:
    return Decision(False, action, PERMISSIONS.get(action, "BLOCKED"), plan, reason)


def _valid_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 1_000_000_000


def _valid_name(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value.strip()))


def validate_plan(plan: Any) -> Decision:
    if not isinstance(plan, dict):
        return _blocked("unknown", "计划必须是 JSON 对象", {})

    action = plan.get("action")
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        return _blocked(str(action), "action 不在工具白名单中", plan)
    extra_fields = set(plan) - ALLOWED_FIELDS
    if extra_fields:
        return _blocked(action, f"计划包含未允许的字段：{sorted(extra_fields)}", plan)

    normalized = {"action": action}
    if action in {"server_info", "service_info", "pm2_info", "general", "unknown"}:
        return Decision(True, action, PERMISSIONS.get(action, "NO_TOOL"), normalized)
    if action in WRITE_ACTIONS:
        if plan.get("name") is None or (isinstance(plan.get("name"), str) and not plan["name"].strip()):
            return Decision(True, action, "NEEDS_INPUT", normalized)
        if not _valid_name(plan.get("name")):
            return _blocked(action, "PM2 操作需要合法的进程名", plan)
        normalized["name"] = plan["name"].strip()
        return Decision(True, action, PERMISSIONS[action], normalized)
    return _blocked(action, "未实现的动作", plan)

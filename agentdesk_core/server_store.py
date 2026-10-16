"""服务器配置的读取、校验与保存（设置页的服务器管理后端）。

与 model_store 同构：服务器列表存 servers.json（含密码，单机个人应用），
保存策略宽松——用户填什么就存什么，只保留结构性底线。
"""

import json
from pathlib import Path
from typing import Any


def read_server_catalog(path: Path) -> dict[str, Any]:
    """读取 servers.json；不存在或损坏时返回空目录。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"default": "", "servers": []}
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        return {"default": "", "servers": []}
    return data


def validate_servers(servers: Any, default: Any = "") -> tuple[dict[str, Any], str]:
    """规范化服务器列表。宽松策略照存，结构性底线：非空列表、id 撞车自动去重。

    _password_draft 是表单新填的密码草稿，覆盖旧值；密码不回显给前端。
    """
    if not isinstance(servers, list):
        return {}, "至少需要保留一个服务器。"
    normalized: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, entry in enumerate(servers, start=1):
        if not isinstance(entry, dict):
            continue
        draft = str(entry.get("_password_draft", "")).strip()
        entry = {k: v for k, v in entry.items() if k != "_password_draft"}
        server_id = str(entry.get("id", "")).strip() or f"server-{index}"
        base_id = server_id
        suffix = 2
        while server_id in used_ids:
            server_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(server_id)
        try:
            port = int(str(entry.get("port", 22)).strip() or 22)
        except (TypeError, ValueError):
            port = 22
        try:
            timeout = float(str(entry.get("connect_timeout", 10)).strip() or 10)
        except (TypeError, ValueError):
            timeout = 10.0
        normalized.append({
            "id": server_id,
            "label": str(entry.get("label", "")).strip() or server_id,
            "host": str(entry.get("host", "")).strip(),
            "port": port,
            "username": str(entry.get("username", "")).strip() or "root",
            "password": draft or str(entry.get("password", "")),
            "connect_timeout": timeout,
        })
    if not normalized:
        return {}, "至少需要保留一个服务器。"
    default_id = str(default or "").strip()
    if not any(entry["id"] == default_id for entry in normalized):
        default_id = normalized[0]["id"]
    return {"default": default_id, "servers": normalized}, ""


def write_server_catalog(path: Path, catalog: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def public_servers(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """给设置页的服务器列表：不回传密码，只标 has_password。"""
    result = []
    for entry in catalog.get("servers", []):
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        item["has_password"] = bool(item.get("password"))
        item.pop("password", None)
        result.append(item)
    return result

"""模型配置的读取、校验与保存（设置页的模型管理后端）。

密钥直接存在 models.json 的 api_key 字段里（单机个人应用，文件权限跟用户目录）。
保存策略宽松：用户填什么就存什么（格式问题在使用时以友好错误提示），
只保留结构性底线——不能清空全部模型、id 撞车自动去重。
"""

import json
from pathlib import Path
from typing import Any


def read_model_catalog(path: Path) -> dict[str, Any]:
    """读取 models.json；不存在或损坏时返回空目录（由调用方决定默认值）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"default": "", "models": []}
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return {"default": "", "models": []}
    return data


def mask_key(value: str) -> str:
    """密钥脱敏：保留前 4 后 4，中间打码。"""
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}••••{value[-4:]}"


def validate_models(models: Any, default: Any = "") -> tuple[dict[str, Any], str]:
    """规范化模型列表。宽松策略：用户填什么就存什么（格式问题在使用时报错），
    只保留两条结构性底线——不能清空全部模型、id 撞车自动去重。"""
    if not isinstance(models, list):
        return {}, "至少需要保留一个模型。"
    normalized: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, entry in enumerate(models, start=1):
        if not isinstance(entry, dict):
            continue
        # key_masked 是回显用的脱敏字段；_key_value 是表单新填的密钥草稿，覆盖旧值。
        draft_key = str(entry.get("_key_value", "")).strip()
        entry = {k: v for k, v in entry.items() if k != "key_masked" and k != "_key_value"}
        model_id = str(entry.get("id", "")).strip() or f"model-{index}"
        base_id = model_id
        suffix = 2
        while model_id in used_ids:
            model_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(model_id)
        normalized.append({
            "id": model_id,
            "label": str(entry.get("label", "")).strip() or model_id,
            "model": str(entry.get("model", "")).strip(),
            "base_url": str(entry.get("base_url", "")).strip().rstrip("/"),
            "api_key": draft_key or str(entry.get("api_key", "")).strip(),
            "wire_api": str(entry.get("wire_api", "")).strip() or "chat",
            "reasoning_effort": str(entry.get("reasoning_effort", "")).strip(),
            "disable_response_storage": bool(entry.get("disable_response_storage", False)),
            "enabled": bool(entry.get("enabled", True)),
            "store": bool(entry.get("store", True)),
        })
    if not normalized:
        return {}, "至少需要保留一个模型。"
    default_id = str(default or "").strip()
    if not any(entry["id"] == default_id for entry in normalized):
        default_id = normalized[0]["id"]
    return {"default": default_id, "models": normalized}, ""


def write_model_catalog(path: Path, catalog: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def public_models(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """给设置页的模型列表：全字段 + 密钥脱敏。"""
    result = []
    for entry in catalog.get("models", []):
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        item["key_masked"] = mask_key(item.get("api_key", ""))
        result.append(item)
    return result

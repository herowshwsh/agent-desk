"""应用配置。所有敏感值从环境变量读取，不写进源码。"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def runtime_root() -> Path:
    """开发模式使用项目目录，打包后使用用户数据目录。"""
    if getattr(sys, "frozen", False):
        return Path.home() / "Library" / "Application Support" / "Agent"
    return PROJECT_ROOT


def packaged_env_candidates() -> list[Path]:
    # 用户数据目录（与 runtime_root 一致）优先：README 推荐的 .env 位置。
    candidates = [Path.home() / "Library" / "Application Support" / "Agent" / ".env"]
    executable_dir = Path(sys.executable).resolve().parent
    candidates.append(Path.cwd() / ".env")
    candidates.append(executable_dir / ".env")
    # .app/Contents/MacOS/<executable> -> 与 .app 同级的目录
    if len(executable_dir.parents) >= 3:
        candidates.append(executable_dir.parents[2] / ".env")
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        candidates.append(Path(bundled_root) / ".env")
    candidates.append(PROJECT_ROOT / ".env")
    return candidates


def _path_from_env(name: str, default: str) -> Path:
    value = os.getenv(name, default).strip()
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def model_config_path() -> Path:
    """当前生效的 models.json 路径（打包版用户目录优先，与桥接进程一致）。"""
    return _path_from_env("AGENT_MODEL_CONFIG", str(runtime_root() / "models.json"))


def servers_config_path() -> Path:
    return _path_from_env("AGENT_SERVERS_CONFIG", str(runtime_root() / "servers.json"))


def _load_server_catalog(path: Path) -> dict[str, Any]:
    """读取 servers.json；宽松——不存在或损坏时返回空目录（服务器不可用时工具会提示）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"default": "", "servers": []}
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        return {"default": "", "servers": []}
    return data


def modules_config_path() -> Path:
    return _path_from_env("AGENT_MODULES_CONFIG", str(runtime_root() / "modules.json"))


def env_file_path() -> Path:
    """当前生效的 .env 路径：模型配置被显式指定时与其同目录（便携/测试场景）；
    否则候选列表里第一个存在的；都没有则用默认。"""
    explicit_config = os.getenv("AGENT_MODEL_CONFIG", "").strip()
    if explicit_config:
        return Path(explicit_config).expanduser().parent / ".env"
    candidates = packaged_env_candidates() if getattr(sys, "frozen", False) else [PROJECT_ROOT / ".env"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return runtime_root() / ".env"


@dataclass(frozen=True)
class ModuleSpec:
    """一个工作模块。agent 模式走工具与审批，chat 模式只做模型转发。"""

    id: str
    label: str
    hint: str
    icon: str
    mode: str
    show_trace: bool
    show_sessions: bool
    model_locked: bool
    allow_files: bool
    welcome_title: str
    welcome_subtitle: str
    placeholder: str
    prompts: tuple[str, ...]
    system_prompt: str
    allow_server: bool = False


@dataclass(frozen=True)
class Settings:
    modules: tuple[ModuleSpec, ...]
    default_module_id: str
    model_id: str
    model_label: str
    model_name: str
    model_base_url: str
    model_api_key: str
    model_wire_api: str
    model_reasoning_effort: str
    model_store: bool
    model_config_path: Path
    available_models: tuple[dict[str, str], ...]
    available_servers: tuple[dict[str, str], ...]
    server_catalog: dict[str, Any]
    server_host: str
    server_username: str
    server_password: str
    server_port: int
    server_connect_timeout: float
    request_timeout: float
    project_dir: Path
    state_file: Path
    audit_file: Path
    trace_file: Path


def _load_model_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"未找到模型配置文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"模型配置文件无法读取：{path}（{error}）") from error
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        raise RuntimeError("模型配置文件格式错误：models 必须是数组。")
    return data


VALID_MODES = {"agent", "chat"}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {"true", "1", "yes", "on"}:
            return True
        if value.lower() in {"false", "0", "no", "off"}:
            return False
    return default


def _as_prompts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if isinstance(item, str) and item.strip())


def _load_modules(path: Path) -> tuple[tuple[ModuleSpec, ...], str]:
    if not path.exists():
        raise RuntimeError(f"未找到模块配置文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"模块配置文件无法读取：{path}（{error}）") from error
    if not isinstance(data, dict) or not isinstance(data.get("modules"), list):
        raise RuntimeError("模块配置文件格式错误：modules 必须是数组。")

    specs: list[ModuleSpec] = []
    for entry in data["modules"]:
        if not isinstance(entry, dict):
            continue
        module_id = str(entry.get("id", "")).strip()
        label = str(entry.get("label", "")).strip()
        mode = str(entry.get("mode", "")).strip()
        if not module_id or not label:
            raise RuntimeError("每个模块都必须配置 id 和 label。")
        if mode not in VALID_MODES:
            raise RuntimeError(f"模块“{label}”的 mode 只能是 {sorted(VALID_MODES)}。")
        if any(spec.id == module_id for spec in specs):
            raise RuntimeError(f"模块 id 重复：{module_id}")
        specs.append(
            ModuleSpec(
                id=module_id,
                label=label,
                hint=str(entry.get("hint", "")).strip(),
                icon=str(entry.get("icon", "chat")).strip() or "chat",
                mode=mode,
                show_trace=_as_bool(entry.get("show_trace"), mode == "agent"),
                show_sessions=_as_bool(entry.get("show_sessions"), True),
                model_locked=_as_bool(entry.get("model_locked"), False),
                allow_files=_as_bool(entry.get("allow_files"), False),
                allow_server=_as_bool(entry.get("allow_server"), False),
                welcome_title=str(entry.get("welcome_title", "")).strip(),
                welcome_subtitle=str(entry.get("welcome_subtitle", "")).strip(),
                placeholder=str(entry.get("placeholder", "")).strip(),
                prompts=_as_prompts(entry.get("prompts")),
                system_prompt=str(entry.get("system_prompt", "")).strip(),
            )
        )

    if not specs:
        raise RuntimeError("模块配置文件里没有任何可用模块。")
    default_module_id = str(data.get("default", specs[0].id)).strip()
    if not any(spec.id == default_module_id for spec in specs):
        default_module_id = specs[0].id
    return tuple(specs), default_module_id


def _select_model(catalog: dict[str, Any], requested_model_id: str = "") -> tuple[str, dict[str, Any]]:
    entries = [entry for entry in catalog["models"] if isinstance(entry, dict) and entry.get("enabled", True)]
    if not entries:
        raise RuntimeError("模型配置文件中没有可用模型。")
    requested = requested_model_id.strip() or os.getenv("AGENT_MODEL_ID", "").strip()
    # 兼容旧版 .env：DEEPSEEK_MODEL 仍可作为临时选择项，但新项目请使用 AGENT_MODEL_ID。
    requested = requested or os.getenv("DEEPSEEK_MODEL", "").strip() or str(catalog.get("default", "")).strip()
    selected = next((entry for entry in entries if entry.get("id") == requested or entry.get("model") == requested), None)
    if selected is None:
        available = ", ".join(str(entry.get("id", "未命名")) for entry in entries)
        raise RuntimeError(f"未找到模型“{requested}”，可用模型：{available}")
    return str(selected.get("id")), selected


def module_by_id(settings: Settings, module_id: str) -> ModuleSpec:
    """按 id 找模块；找不到或为空时回落到默认模块。"""
    requested = module_id.strip()
    for spec in settings.modules:
        if spec.id == requested:
            return spec
    for spec in settings.modules:
        if spec.id == settings.default_module_id:
            return spec
    return settings.modules[0]


def load_settings(model_id: str = "") -> Settings:
    app_root = runtime_root()
    env_candidates = packaged_env_candidates() if getattr(sys, "frozen", False) else [PROJECT_ROOT / ".env"]
    for env_file in env_candidates:
        if env_file.exists():
            load_dotenv(env_file)
            break
    model_config_path = _path_from_env("AGENT_MODEL_CONFIG", str(app_root / "models.json"))
    catalog = _load_model_config(model_config_path)
    selected_model_id, model = _select_model(catalog, model_id)
    modules_config_path = _path_from_env("AGENT_MODULES_CONFIG", str(app_root / "modules.json"))
    modules, default_module_id = _load_modules(modules_config_path)
    # 密钥直接存在 models.json 的 api_key 字段里（设置页填写）。
    api_key = str(model.get("api_key", "")).strip()
    if not api_key:
        raise RuntimeError(f"模型“{selected_model_id}”还没有配置密钥，请在设置 → 模型管理里填写。")
    wire_api = str(model.get("wire_api", "chat")).strip() or "chat"
    if wire_api not in {"chat", "responses"}:
        raise RuntimeError(f"模型“{selected_model_id}”的 wire_api 只能是 chat 或 responses。")

    # 服务器：来自设置页维护的 servers.json（default 指定的那台）。
    server_catalog = _load_server_catalog(servers_config_path())
    server_default = str(server_catalog.get("default", "")).strip()
    selected_server = next(
        (entry for entry in server_catalog["servers"] if entry["id"] == server_default),
        server_catalog["servers"][0] if server_catalog["servers"] else {},
    )
    available_servers = tuple(
        {"id": str(entry.get("id")), "label": str(entry.get("label") or entry.get("host", entry.get("id")))}
        for entry in server_catalog["servers"]
        if isinstance(entry, dict) and entry.get("id")
    )

    available_models = tuple(
        {
            "id": str(entry.get("id")),
            "label": str(entry.get("label", entry.get("id"))),
            "model": str(entry.get("model", entry.get("id"))),
        }
        for entry in catalog["models"]
        if isinstance(entry, dict) and entry.get("enabled", True) and entry.get("id")
    )

    return Settings(
        modules=modules,
        default_module_id=default_module_id,
        model_id=selected_model_id,
        model_label=str(model.get("label", selected_model_id)),
        model_name=str(model.get("model", selected_model_id)),
        model_base_url=str(model.get("base_url", "")).strip(),
        model_api_key=api_key,
        model_wire_api=str(model.get("wire_api", "chat")).strip() or "chat",
        model_reasoning_effort=str(model.get("reasoning_effort", "")).strip(),
        model_store=not _as_bool(model.get("disable_response_storage"), False),
        model_config_path=model_config_path,
        available_models=available_models,
        available_servers=available_servers,
        server_catalog=server_catalog,
        server_host=str(selected_server.get("host", "")),
        server_username=str(selected_server.get("username", "root")),
        server_password=str(selected_server.get("password", "")),
        server_port=int(selected_server.get("port", 22)),
        server_connect_timeout=float(selected_server.get("connect_timeout", 10)),
        request_timeout=float(os.getenv("AGENT_REQUEST_TIMEOUT", "45")),
        project_dir=_path_from_env("AGENT_PROJECT_DIR", str(app_root)),
        state_file=_path_from_env("AGENT_STATE_FILE", str(app_root / "agentdesk_memory.json")),
        audit_file=_path_from_env("AGENT_AUDIT_FILE", str(app_root / "agentdesk_audit.jsonl")),
        trace_file=_path_from_env("AGENT_TRACE_FILE", str(app_root / "agentdesk_trace.jsonl")),
    )

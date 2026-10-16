"""AgentDesk 与桌面前端之间的 JSON Lines 桥接进程。"""

import json
import os
import sys
import threading
import uuid
from typing import Any, Dict

from agentdesk_core.agent import AgentDesk, friendly_model_error
from agentdesk_core import model_store, server_store
from agentdesk_core.config import load_settings, model_config_path, module_by_id, servers_config_path


def emit(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class DesktopBridge:
    def __init__(self) -> None:
        self.pending: Dict[str, bool] = {}
        self.pending_events: Dict[str, threading.Event] = {}
        self.pending_by_request: Dict[str, str] = {}
        self.active_requests: Dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.request_context = threading.local()
        self.reload()

    def reload(self) -> None:
        """按当前磁盘配置重建模型实例；配置损坏时桥保持存活，供设置页修复。"""
        try:
            self.settings = load_settings()
            self.startup_error = ""
            self.agent = AgentDesk(self.settings, confirm=self.confirm)
            self.agents = {self.settings.model_id: self.agent}
        except Exception as error:
            self.settings = None
            self.startup_error = str(error)
            self.agent = None
            self.agents = {}

    def agent_for_model(self, model_id: str) -> AgentDesk:
        requested = model_id.strip()
        if not requested or requested == self.settings.model_id:
            return self.agent
        with self.lock:
            if requested in self.agents:
                return self.agents[requested]
            selected_settings = load_settings(requested)
            selected_agent = AgentDesk(selected_settings, confirm=self.confirm)
            self.agents[requested] = selected_agent
            return selected_agent

    def agent_for(self, module, model_id: str) -> AgentDesk:
        """按模块的 model_locked 决定能否切换模型。"""
        if module.model_locked:
            return self.agent
        return self.agent_for_model(model_id)

    def confirm(self, action: str, plan: Dict[str, Any]) -> bool:
        request_id = f"approval-{uuid.uuid4().hex[:10]}"
        event = threading.Event()
        owner_id = getattr(self.request_context, "request_id", "")
        with self.lock:
            owner_cancelled = self.active_requests.get(owner_id)
            if owner_cancelled and owner_cancelled.is_set():
                return False
            self.pending[request_id] = False
            self.pending_events[request_id] = event
            if owner_id:
                self.pending_by_request[owner_id] = request_id
        emit({"type": "approval_request", "id": request_id, "request_id": owner_id, "action": action, "plan": plan})
        event.wait()
        with self.lock:
            approved = self.pending.pop(request_id, False)
            self.pending_events.pop(request_id, None)
            if owner_id:
                self.pending_by_request.pop(owner_id, None)
        return approved

    def run_chat(self, request_id: str, question: str, module_id: str, model_id: str, history: Any, cancel_event: threading.Event, workspace: str = "", server_id: str = "") -> None:
        self.request_context.request_id = request_id
        on_delta = lambda text: emit({"type": "chat_delta", "id": request_id, "module": module_id, "text": text})
        on_stage = lambda stage, detail: emit({"type": "chat_stage", "id": request_id, "module": module_id, "stage": stage, "detail": detail})
        if self.settings is None:
            emit({"type": "chat_done", "id": request_id, "module": module_id,
                  "answer": f"模型配置无效，无法开始对话：{self.startup_error}\n\n请到「设置 → 模型管理」修复配置后重试。"})
            return
        try:
            module = module_by_id(self.settings, module_id)
            agent = self.agent_for(module, model_id)
            agent.select_server(str(server_id or ""))
            if module.mode == "chat":
                answer = agent.run_chat(question, module, history, is_cancelled=cancel_event.is_set, workspace=workspace if module.allow_files else "", on_delta=on_delta, on_stage=on_stage)
            else:
                answer = agent.run_turn(question, is_cancelled=cancel_event.is_set, on_delta=on_delta, on_stage=on_stage)
            if cancel_event.is_set():
                answer = "已取消当前思考，未执行服务器操作。" if module.mode == "agent" else "已取消当前思考，未发送任何内容。"
            emit({"type": "chat_done", "id": request_id, "module": module.id, "answer": answer})
        except Exception as error:
            emit({"type": "chat_done", "id": request_id, "answer": f"请求失败：{friendly_model_error(error)}"})
        finally:
            with self.lock:
                self.active_requests.pop(request_id, None)
            self.request_context.request_id = ""

    def handle(self, payload: Dict[str, Any]) -> None:
        message_type = payload.get("type")
        if message_type == "get_config":
            if self.settings is None:
                emit({
                    "type": "app_config",
                    "model": "配置无效",
                    "model_id": "",
                    "models": [],
                    "modules": [],
                    "default_module_id": "",
                    "config_error": self.startup_error,
                })
                return
            emit({
                "type": "app_config",
                "model": self.settings.model_label,
                "model_id": self.settings.model_id,
                "models": list(self.settings.available_models),
                "servers": list(self.settings.available_servers),
                "modules": [
                    {
                        "id": module.id,
                        "label": module.label,
                        "hint": module.hint,
                        "icon": module.icon,
                        "mode": module.mode,
                        "show_trace": module.show_trace,
                        "show_sessions": module.show_sessions,
                        "model_locked": module.model_locked,
                        "allow_files": module.allow_files,
                        "welcome_title": module.welcome_title,
                        "welcome_subtitle": module.welcome_subtitle,
                        "placeholder": module.placeholder,
                        "prompts": list(module.prompts),
                    }
                    for module in self.settings.modules
                ],
                "default_module_id": self.settings.default_module_id,
            })
            return
        if message_type == "get_models":
            self._send_models_config()
            return
        if message_type == "save_models":
            self._save_models(payload)
            return
        if message_type == "get_servers":
            self._send_servers_config()
            return
        if message_type == "save_servers":
            self._save_servers(payload)
            return
        if message_type == "test_server":
            self._test_server(payload)
            return
        if message_type == "approval":
            request_id = str(payload.get("id", ""))
            with self.lock:
                if request_id in self.pending:
                    self.pending[request_id] = bool(payload.get("approved"))
                    self.pending_events[request_id].set()
            return
        if message_type == "cancel_chat":
            request_id = str(payload.get("id", ""))
            with self.lock:
                cancel_event = self.active_requests.get(request_id)
                if cancel_event:
                    cancel_event.set()
                approval_id = self.pending_by_request.get(request_id)
                if approval_id and approval_id in self.pending:
                    self.pending[approval_id] = False
                    self.pending_events[approval_id].set()
            return
        if message_type != "chat":
            return

        request_id = str(payload.get("id", uuid.uuid4().hex))
        question = str(payload.get("question", "")).strip()
        module_id = str(payload.get("module_id", "")).strip()
        module = module_by_id(self.settings, module_id)
        if not question:
            emit({"type": "chat_done", "id": request_id, "module": module.id, "answer": "请输入内容后再发送。"})
            return
        status_message = "正在理解你的任务" if module.mode == "agent" else "正在组织回复"
        emit({"type": "status", "id": request_id, "module": module.id, "stage": "thinking", "message": status_message})
        emit({"type": "chat_stage", "id": request_id, "module": module.id, "stage": "queued", "detail": ""})
        cancel_event = threading.Event()
        model_id = str(payload.get("model_id", "")).strip()
        history = payload.get("history", [])
        with self.lock:
            self.active_requests[request_id] = cancel_event
        threading.Thread(
            target=self.run_chat,
            args=(request_id, question, module.id, model_id, history, cancel_event, str(payload.get("workspace", "") or ""), str(payload.get("server_id", "") or "")),
            daemon=True,
        ).start()

    def _send_models_config(self) -> None:
        """给设置页的模型管理：完整模型列表（密钥脱敏）。"""
        try:
            catalog = model_store.read_model_catalog(model_config_path())
            emit({
                "type": "models_config",
                "models": model_store.public_models(catalog),
                "default": str(catalog.get("default", "")),
                "models_path": str(model_config_path()),
            })
        except Exception as error:
            emit({"type": "models_config_error", "message": friendly_model_error(error)})

    def _send_servers_config(self) -> None:
        """给设置页的服务器管理：完整列表（密码不回传）。"""
        try:
            catalog = server_store.read_server_catalog(servers_config_path())
            emit({
                "type": "servers_config",
                "servers": server_store.public_servers(catalog),
                "default": str(catalog.get("default", "")),
                "servers_path": str(servers_config_path()),
            })
        except Exception as error:
            emit({"type": "servers_config_error", "message": friendly_model_error(error)})

    def _save_servers(self, payload: Dict[str, Any]) -> None:
        """保存服务器列表：校验 → 写 servers.json → 热重载。

        前端不回传旧密码（表单留空即不改），保存前按 id 从磁盘回填，防止清空。
        """
        try:
            servers = payload.get("servers")
            old_passwords = {}
            old_catalog = server_store.read_server_catalog(servers_config_path())
            for item in old_catalog.get("servers", []):
                if isinstance(item, dict) and item.get("id"):
                    old_passwords[str(item["id"])] = str(item.get("password", ""))
            if isinstance(servers, list):
                for entry in servers:
                    if isinstance(entry, dict) and not str(entry.get("password", "")):
                        entry["password"] = old_passwords.get(str(entry.get("id", "")), "")
            catalog, error = server_store.validate_servers(servers, payload.get("default"))
            if error:
                emit({"type": "servers_save_error", "message": error})
                return
            server_store.write_server_catalog(servers_config_path(), catalog)
            self.reload()
            self._send_servers_config()
            emit({"type": "servers_saved", "message": ""})
        except Exception as error:
            emit({"type": "servers_save_error", "message": friendly_model_error(error)})

    def _test_server(self, payload: Dict[str, Any]) -> None:
        """测试连接：表单新填的密码优先，留空时用磁盘已保存的密码（与服务器助手同源）。"""
        from agentdesk_core.server_client import ServerClient

        servers = payload.get("servers") or []
        entry = {}
        for item in servers:
            if isinstance(item, dict) and str(item.get("id", "")) == str(payload.get("id", "")):
                entry = item
                break
        if not entry:
            catalog = server_store.read_server_catalog(servers_config_path())
            for item in catalog.get("servers", []):
                if str(item.get("id", "")) == str(payload.get("id", "")):
                    entry = item
                    break
        if not isinstance(entry, dict) or not str(entry.get("host", "")).strip():
            emit({"type": "server_test_result", "id": payload.get("id", ""), "ok": False, "message": "请先填写服务器地址。"})
            return
        # 密码：表单草稿优先，留空回填磁盘已保存的（前端不回显密码）。
        password = str(entry.get("_password_draft", "")).strip()
        if not password:
            saved = server_store.read_server_catalog(servers_config_path())
            for item in saved.get("servers", []):
                if isinstance(item, dict) and str(item.get("id", "")) == str(payload.get("id", "")):
                    password = str(item.get("password", ""))
                    break
        try:
            port = int(str(entry.get("port", 22)).strip() or 22)
        except (TypeError, ValueError):
            port = 22
        client = ServerClient(
            host=str(entry.get("host", "")).strip(),
            username=str(entry.get("username", "")).strip() or "root",
            password=password,
            port=port,
            timeout=10.0,
        )
        result = client.test_connection()
        emit({"type": "server_test_result", "id": payload.get("id", ""), **result})

    def _save_models(self, payload: Dict[str, Any]) -> None:
        """保存设置页提交的模型列表（含密钥）：校验 → 写 models.json → 热重载。"""
        try:
            models = payload.get("models")
            catalog, error = model_store.validate_models(models, payload.get("default"))
            if error:
                emit({"type": "models_save_error", "message": error})
                return
            model_store.write_model_catalog(model_config_path(), catalog)
            self.reload()
            self._send_models_config()
            self.handle({"type": "get_config"})
            emit({"type": "models_saved", "message": ""})
        except Exception as error:
            emit({"type": "models_save_error", "message": friendly_model_error(error)})

    def close(self) -> None:
        with self.lock:
            for cancel_event in self.active_requests.values():
                cancel_event.set()
        for agent in self.agents.values():
            agent.close()


def main() -> None:
    try:
        bridge = DesktopBridge()
    except Exception as error:
        emit({"type": "bridge_error", "message": str(error)})
        return

    try:
        for line in sys.stdin:
            if line.strip():
                bridge.handle(json.loads(line))
    except (BrokenPipeError, EOFError, KeyboardInterrupt):
        pass
    finally:
        bridge.close()


if __name__ == "__main__":
    main()

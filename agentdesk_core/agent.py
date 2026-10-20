"""AgentDesk 的核心工作流。"""

import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from typing import Any, Callable, Dict, Optional

from openai import OpenAI

from .config import ModuleSpec, Settings
from .attachments import build_file_tree, edit_workspace_file, read_workspace_file, write_workspace_file
from .guardrails import Decision, validate_plan
from .intent_rag import infer_pm2_plan, retrieve_routing_context
from .memory import MemoryStore
from .server_client import ServerClient
from .tool_registry import OPERATION_TO_ACTION, SERVER_TOOLS_BY_NAME, to_chat_tool_schemas
from .tracing import append_jsonl


Confirm = Callable[[str, Dict[str, Any]], bool]
WRITE_ACTIONS = {"pm2_start", "pm2_stop", "pm2_restart"}
CHAT_HISTORY_LIMIT = 12
CHAT_TOOL_ROUNDS = 4
SERVER_TOOL_ROUNDS = 3
_WEEKDAYS = "一二三四五六日"


def time_context() -> str:
    """把当前日期/时间注入系统提示——模型本身没有时钟，不注入就答不出"今天周几"。"""
    now = datetime.now()
    return (
        f"当前时间：{now:%Y-%m-%d %H:%M}（星期{_WEEKDAYS[now.weekday()]}，本机时区）。"
        "涉及日期、星期、几点等时间问题时，直接以此为准回答，不要声称无法获取实时日期。"
    )


TURN_SYSTEM_PROMPT = (
    "你是 AgentDesk 的服务器助手，通过工具查询和维护用户的服务器。"
    "根据用户的问题选择合适的工具；没有合适工具时直接用已有知识回答，并说明这是无法通过工具核实的一般性回答。"
    "用户没有给出 PM2 进程名时，manage_pm2_process 的 name 传空字符串，并在回答里向用户询问进程名。"
    "工具返回的是 JSON 结果，回答时用清晰的中文组织，不要罗列原始 JSON。"
)

READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取工作空间内文本文件的内容。只能读工作空间里的文件，path 为文件树中列出的相对路径。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于工作空间根目录的文件路径"},
            },
            "required": ["path"],
        },
    },
}

WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "把内容写入工作空间内的文本文件（覆盖整个文件）。这是敏感写操作："
            "调用后需要用户在界面确认才会真正写入；写入前系统会自动备份原文件。"
            "content 必须是文件的完整新内容，不要只写改动片段。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于工作空间根目录的文件路径"},
                "content": {"type": "string", "description": "文件的完整新内容"},
            },
            "required": ["path", "content"],
        },
    },
}

EDIT_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "编辑工作空间内已有的文本文件：把文件中唯一出现的 old_text 替换为 new_text。"
            "这是敏感写操作：调用后需要用户在界面确认才会真正写入，系统会自动备份原文件。"
            "old_text 必须与文件内容逐字符一致（含空格缩进）且在文件中唯一；"
            "修改大文件时优先用本工具而不是重写整个文件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于工作空间根目录的文件路径"},
                "old_text": {"type": "string", "description": "要被替换的原文片段（必须唯一）"},
                "new_text": {"type": "string", "description": "替换后的内容；纯删除时传空字符串"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
}


def _history_messages(history: Any) -> list[Dict[str, str]]:
    """把前端传来的历史裁成模型可用的 messages，忽略非法项。"""
    if not isinstance(history, list):
        return []
    messages: list[Dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        text = str(item.get("text", "")).strip()
        if role in {"user", "assistant"} and text:
            messages.append({"role": role, "content": text})
    return messages[-CHAT_HISTORY_LIMIT:]


def friendly_model_error(error: Exception) -> str:
    """把模型服务的异常翻译成用户能懂的一句话，附原始信息便于排查。"""
    kind = type(error).__name__
    text = str(error)
    lowered = text.lower()
    if "timeout" in kind.lower() or "timed out" in lowered:
        reason = "模型服务响应超时"
    elif "connection" in kind.lower() or "connect" in lowered and "error" in lowered:
        reason = "无法连接模型服务，请检查网络或接口地址"
    elif "auth" in kind.lower() or "401" in text or "invalid api key" in lowered or "unauthorized" in lowered:
        reason = "模型密钥无效或过期，请检查 .env 里的密钥"
    elif "429" in text or "rate" in kind.lower() or "quota" in lowered:
        reason = "请求太频繁，模型服务限流了，请稍等再试"
    elif "404" in text or "not found" in lowered or "model" in lowered and "not exist" in lowered:
        reason = "模型名称不存在或接口地址不对，请检查 models.json"
    elif "500" in text or "502" in text or "503" in text or "server error" in lowered:
        reason = "模型服务端出错了，请稍后重试"
    else:
        reason = "模型服务返回异常"
    return f"{reason}（{kind}: {text[:160]}）"


def is_retryable_error(error: Exception) -> bool:
    """瞬时错误（超时/连接/限流/服务端故障）值得自动重试；密钥、路径配错重试也没用。"""
    kind = type(error).__name__.lower()
    text = str(error).lower()
    if "auth" in kind or "401" in text or "invalid api key" in text:
        return False
    if "404" in text or "not found" in text or "not exist" in text:
        return False
    return True


class AgentDesk:
    def __init__(self, settings: Settings, confirm: Optional[Confirm] = None) -> None:
        self.settings = settings
        client_options = {"api_key": settings.model_api_key}
        if settings.model_base_url:
            client_options["base_url"] = settings.model_base_url
        # 第三方服务 hang 住时不能无限等：单次请求 45 秒超时，最多重试 1 次。
        client_options["timeout"] = settings.request_timeout
        client_options["max_retries"] = 1
        # 连接池保活 5 分钟：默认 5 秒，间隔稍久就重新 DNS+TLS 握手，白白多几百毫秒。
        client_options["http_client"] = httpx.Client(
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=300),
        )
        self.client = OpenAI(**client_options)
        self.server_catalog = getattr(settings, "server_catalog", {}) or {}
        self.server = self._build_server(self._selected_server_entry())
        self.memory = MemoryStore(settings.state_file)
        self.confirm = confirm or self._interactive_confirm

    def _selected_server_entry(self) -> Dict[str, Any]:
        servers = self.server_catalog.get("servers", [])
        default_id = str(self.server_catalog.get("default", ""))
        return next((entry for entry in servers if entry.get("id") == default_id), servers[0] if servers else {})

    def _build_server(self, entry: Dict[str, Any]) -> ServerClient:
        return ServerClient(
            str(entry.get("host", "")),
            str(entry.get("username", "root")),
            str(entry.get("password", "")),
            int(entry.get("port", 22)),
            float(entry.get("connect_timeout", 10)),
        )

    def select_server(self, server_id: str) -> None:
        """服务器助手按界面选择切换目标机器；未知 id 保持现状。"""
        requested = str(server_id or "").strip()
        if not requested:
            return
        entry = next((entry for entry in self.server_catalog.get("servers", []) if entry.get("id") == requested), None)
        if entry is not None:
            self.server = self._build_server(entry)

    def close(self) -> None:
        return None

    def _interactive_confirm(self, action: str, plan: Dict[str, Any]) -> bool:
        operation = action.removeprefix("pm2_")
        print(f"准备在服务器上执行：pm2 {operation} {plan['name']}")
        answer = input("这是敏感操作，输入 yes / 确认 才会执行：").strip().lower()
        return answer in {"yes", "y", "确认", "是"}

    # ---------- 统一模型调用层 ----------
    # items 为中立消息格式，屏蔽 chat completions 与 responses 两种协议差异：
    #   {"role": "user"|"assistant", "content": str}
    #   {"role": "tool_calls", "content": str, "calls": [{"id","name","arguments"}]}
    #   {"role": "tool_result", "call_id": str, "content": str}
    # 返回 {"content": str, "tool_calls": [{"id","name","arguments"}]}

    def _to_chat_messages(self, system: str, items: list) -> list:
        messages = [{"role": "system", "content": system}] if system else []
        for item in items:
            role = item.get("role")
            if role in {"user", "assistant"}:
                messages.append({"role": role, "content": item.get("content", "")})
            elif role == "tool_calls":
                calls = [
                    {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}}
                    for call in item.get("calls", [])
                ]
                messages.append({"role": "assistant", "content": item.get("content") or "", "tool_calls": calls})
            elif role == "tool_result":
                messages.append({"role": "tool", "tool_call_id": item.get("call_id", ""), "content": item.get("content", "")})
        return messages

    def _to_responses_input(self, items: list) -> list:
        input_items: list = []
        for item in items:
            role = item.get("role")
            if role in {"user", "assistant"}:
                content = item.get("content", "")
                if content:
                    input_items.append({"role": role, "content": content})
            elif role == "tool_calls":
                if item.get("content"):
                    input_items.append({"role": "assistant", "content": item["content"]})
                for call in item.get("calls", []):
                    input_items.append({"type": "function_call", "call_id": call["id"], "name": call["name"], "arguments": call["arguments"]})
            elif role == "tool_result":
                input_items.append({"type": "function_call_output", "call_id": item.get("call_id", ""), "output": item.get("content", "")})
        return input_items

    def _create_with_retry(self, create: Callable[[], Any], on_stage: Optional[Callable[[str, str], None]]) -> Any:
        """流式请求发出前的重试：只包 create() 本身（此刻还没吐任何字，重放安全）。"""
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                return create()
            except Exception as error:
                last_error = error
                if attempt == 0 and is_retryable_error(error):
                    if on_stage:
                        on_stage("retrying", friendly_model_error(error).split("（")[0])
                    time.sleep(1.5)
                    continue
                raise
        raise last_error  # pragma: no cover - 循环内必然 return 或 raise

    def _completion(self, system: str, items: list, tools: Any = None, json_mode: bool = False, on_delta: Optional[Callable[[str], None]] = None, on_stage: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        if self.settings.model_wire_api == "responses":
            return self._completion_responses(system, items, tools, json_mode, on_delta, on_stage)
        kwargs: Dict[str, Any] = {"model": self.settings.model_name, "messages": self._to_chat_messages(system, items)}
        if tools:
            kwargs["tools"] = tools
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if not on_delta:
            response = self._create_with_retry(lambda: self.client.chat.completions.create(**kwargs), on_stage)
            message = response.choices[0].message
            calls = [
                {"id": call.id, "name": call.function.name, "arguments": call.function.arguments}
                for call in (getattr(message, "tool_calls", None) or [])
            ]
            return {"content": message.content or "", "tool_calls": calls}
        kwargs["stream"] = True
        content = ""
        stage = ["connecting"]
        calls_by_index: Dict[int, Dict[str, str]] = {}
        ttfb = {"first": None, "start": time.perf_counter()}
        if on_stage:
            on_stage("connecting", "")

        def note_first_event() -> None:
            if ttfb["first"] is None:
                ttfb["first"] = round((time.perf_counter() - ttfb["start"]) * 1000)

        stream = self._create_with_retry(lambda: self.client.chat.completions.create(**kwargs), on_stage)
        for chunk in stream:
            note_first_event()
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning and stage[0] not in {"thinking", "generating"}:
                stage[0] = "thinking"
                if on_stage:
                    on_stage(stage[0], "")
            if piece:
                if stage[0] != "generating":
                    stage[0] = "generating"
                    if on_stage:
                        on_stage(stage[0], "")
                content += piece
                on_delta(piece)
            for tool_delta in getattr(delta, "tool_calls", None) or []:
                entry = calls_by_index.setdefault(tool_delta.index, {"id": "", "name": "", "arguments": ""})
                if getattr(tool_delta, "id", None):
                    entry["id"] = tool_delta.id
                function = getattr(tool_delta, "function", None)
                if function and getattr(function, "name", None):
                    entry["name"] = function.name
                if function and getattr(function, "arguments", None):
                    entry["arguments"] += function.arguments
        return {"content": content, "tool_calls": list(calls_by_index.values()), "ttfb_ms": ttfb["first"]}

    def _completion_responses(self, system: str, items: list, tools: Any = None, json_mode: bool = False, on_delta: Optional[Callable[[str], None]] = None, on_stage: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.settings.model_name,
            "input": self._to_responses_input(items),
            "store": self.settings.model_store,
        }
        if system:
            kwargs["instructions"] = system
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "parameters": tool["function"].get("parameters", {}),
                }
                for tool in tools
            ]
        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}
        if self.settings.model_reasoning_effort:
            kwargs["reasoning"] = {"effort": self.settings.model_reasoning_effort}
        if not on_delta:
            response = self._create_with_retry(lambda: self.client.responses.create(**kwargs), on_stage)
            content = getattr(response, "output_text", "") or ""
            calls = [
                {"id": item.call_id, "name": item.name, "arguments": item.arguments}
                for item in (getattr(response, "output", None) or [])
                if getattr(item, "type", "") == "function_call"
            ]
            return {"content": content, "tool_calls": calls}
        kwargs["stream"] = True
        content = ""
        stage = ["connecting"]
        calls_by_index: Dict[int, Dict[str, str]] = {}
        ttfb = {"first": None, "start": time.perf_counter()}

        def enter(next_stage: str) -> None:
            if stage[0] != next_stage:
                stage[0] = next_stage
                if on_stage:
                    on_stage(next_stage, "")

        if on_stage:
            on_stage("connecting", "")
        stream = self._create_with_retry(lambda: self.client.responses.create(**kwargs), on_stage)
        for event in stream:
            if ttfb["first"] is None:
                ttfb["first"] = round((time.perf_counter() - ttfb["start"]) * 1000)
            event_type = getattr(event, "type", "")
            if event_type.startswith("response.reasoning"):
                enter("thinking")
            elif event_type == "response.output_text.delta":
                enter("generating")
                piece = getattr(event, "delta", "")
                if piece:
                    content += piece
                    on_delta(piece)
            elif event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if getattr(item, "type", "") == "function_call":
                    enter("thinking")
                    calls_by_index[event.output_index] = {
                        "id": getattr(item, "call_id", ""),
                        "name": getattr(item, "name", ""),
                        "arguments": getattr(item, "arguments", "") or "",
                    }
            elif event_type == "response.function_call_arguments.delta":
                entry = calls_by_index.get(event.output_index)
                if entry is not None:
                    entry["arguments"] += getattr(event, "delta", "") or ""
        return {"content": content, "tool_calls": list(calls_by_index.values()), "ttfb_ms": ttfb["first"]}

    def _answer(self, question: str, source: Any, on_delta: Optional[Callable[[str], None]] = None, on_stage: Optional[Callable[[str, str], None]] = None) -> str:
        try:
            if on_stage:
                on_stage("connecting", "")
            result = self._completion(
                "只根据工作流结果回答，不要编造数据。明确说明取消、拦截或失败。",
                [{"role": "user", "content": f"问题：{question}\n工作流结果：{json.dumps(source, ensure_ascii=False)}"}],
                on_delta=on_delta,
                on_stage=on_stage,
            )
            return result["content"] or "模型没有返回文本。"
        except Exception:
            return json.dumps(source, ensure_ascii=False)

    def _execute(self, decision: Decision) -> Dict[str, Any]:
        action = decision.action
        if action == "server_info":
            return self.server.query()
        if action == "service_info":
            return self.server.query_services()
        if action == "pm2_info":
            return self.server.query_pm2()
        if action in WRITE_ACTIONS:
            if decision.permission == "NEEDS_INPUT":
                return {"ok": False, "needs_input": True, "message": "请告诉我需要操作的 PM2 进程名。"}
            if not self.confirm(action, decision.plan):
                return {"ok": False, "cancelled": True, "message": "用户未授权，未执行服务器操作。"}
            return self.server.manage_pm2(action, decision.plan["name"])
        return {"ok": True, "data": "这是一个普通问题，不需要调用工具。"}

    @staticmethod
    def _write_authorized(items: list) -> bool:
        """两步写入授权：历史里必须存在 [写入提案]，且提案之后用户有过回应。"""
        proposal_seen = False
        for item in items:
            role = item.get("role")
            text = str(item.get("content", ""))
            if role == "assistant" and "[写入提案]" in text:
                proposal_seen = True
            elif role == "user" and proposal_seen:
                return True
        return False

    def _handle_write_file(self, arguments: Dict[str, Any], items: list, workspace_root: Path, trace: Dict[str, Any]) -> str:
        """write_file 工具回调：仅在用户已确认提案后执行，落盘自动备份并记审计。"""
        rel_path = str(arguments.get("path", "")).strip()
        content = arguments.get("content")
        if not self._write_authorized(items):
            append_jsonl(self.settings.audit_file, {"action": "write_file", "plan": {"path": rel_path}, "status": "unauthorized"})
            return ("错误：用户还没有确认这次写入。请先输出以 [写入提案] 开头的提案"
                    "（目标文件、理由、怎么改），等用户在界面确认后再调用本工具。")
        # 越界/缺参先拦下，不打扰用户；write_workspace_file 内部还有完整校验兜底。
        if not rel_path:
            return "错误：缺少 path 参数。"
        if Path(rel_path).is_absolute() or ".." in Path(rel_path).parts:
            append_jsonl(self.settings.audit_file, {"action": "write_file", "plan": {"path": rel_path}, "status": "blocked"})
            return f"错误：{rel_path} 不在工作空间内，只能写工作空间里的文件。"
        if not isinstance(content, str) or not content:
            return "错误：缺少 content 参数（要写入的完整内容）。"
        message, status = write_workspace_file(workspace_root, rel_path, content)
        append_jsonl(self.settings.audit_file, {"action": "write_file", "plan": {"path": rel_path, "bytes": len(content.encode('utf-8'))}, "status": status})
        if status == "ok":
            trace["wrote_file"] = rel_path
        return message

    def _handle_edit_file(self, arguments: Dict[str, Any], items: list, workspace_root: Path, trace: Dict[str, Any]) -> str:
        """edit_file 工具回调：仅在用户已确认提案后执行，唯一片段替换并自动备份。"""
        rel_path = str(arguments.get("path", "")).strip()
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if not self._write_authorized(items):
            append_jsonl(self.settings.audit_file, {"action": "edit_file", "plan": {"path": rel_path}, "status": "unauthorized"})
            return ("错误：用户还没有确认这次编辑。请先输出以 [写入提案] 开头的提案"
                    "（目标文件、理由、怎么改），等用户在界面确认后再调用本工具。")
        if not rel_path:
            return "错误：缺少 path 参数。"
        if Path(rel_path).is_absolute() or ".." in Path(rel_path).parts:
            append_jsonl(self.settings.audit_file, {"action": "edit_file", "plan": {"path": rel_path}, "status": "blocked"})
            return f"错误：{rel_path} 不在工作空间内，只能编辑工作空间里的文件。"
        if not isinstance(old_text, str) or not old_text:
            return "错误：缺少 old_text 参数（要被替换的原文片段）。"
        if not isinstance(new_text, str):
            return "错误：缺少 new_text 参数（替换后的内容，纯删除时传空字符串）。"

        current = read_workspace_file(workspace_root, rel_path)
        if current.startswith("错误："):
            return current
        occurrences = current.count(old_text)
        if occurrences == 0:
            return "错误：文件里没有找到要替换的片段。请先用 read_file 核对内容（注意空格与缩进），再重试。"
        if occurrences > 1:
            return f"错误：要替换的片段在文件里出现了 {occurrences} 次。请扩大片段范围，保证唯一。"

        message, status = edit_workspace_file(workspace_root, rel_path, old_text, new_text)
        append_jsonl(self.settings.audit_file, {"action": "edit_file", "plan": {"path": rel_path}, "status": status})
        if status == "ok":
            trace["edited_file"] = rel_path
        return message

    def run_chat(
        self,
        question: str,
        module: ModuleSpec,
        history: Any = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        workspace: str = "",
        on_delta: Optional[Callable[[str], None]] = None,
        on_stage: Optional[Callable[[str, str], None]] = None,
    ) -> str:
        """纯转发模式：只把对话交给模型，不路由意图、不写审计。

        选中工作空间时给模型 read_file 工具：先发文件树，模型按需请求读文件，
        bridge 读内容回填，最多 CHAT_TOOL_ROUNDS 轮。路径限制在工作空间内。
        """
        started = time.perf_counter()
        cancelled = is_cancelled or (lambda: False)
        trace: Dict[str, Any] = {"module": module.id, "mode": "chat", "question_length": len(question)}
        system_prompt = (
            f"{time_context()}\n\n"
            + (module.system_prompt or "你是一个乐于助人的对话助手。")
        )
        allow_server = bool(getattr(module, "allow_server", False))
        if allow_server:
            system_prompt += (
                "\n\n你可以调用工具直接查询用户配置的服务器："
                "get_system_info（系统信息）、get_services（运行中的服务）、"
                "get_pm2_processes（PM2 进程）。涉及服务器状态的问题优先用工具核实，不要凭猜测回答；"
                "工具返回 JSON，回答时用清晰中文组织。启动/停止/重启 PM2 属于写操作，"
                "告诉用户去「服务器助手」执行（那里有确认流程）。"
            )
        items: list = [
            {"role": message["role"], "content": message["content"]}
            for message in _history_messages(history)
        ]
        user_content = question
        tools = None
        workspace_root = None
        if allow_server:
            tools = to_chat_tool_schemas(read_only_only=True)
        if workspace:
            from .attachments import build_file_tree

            workspace_root = Path(workspace).expanduser()
            if workspace_root.is_dir():
                tools = (tools or []) + [READ_FILE_TOOL, WRITE_FILE_TOOL, EDIT_FILE_TOOL]
                user_content = (
                    f"{question}\n\n---\n"
                    f"{build_file_tree(workspace_root)}\n"
                    "需要看某个文件的具体内容时，用 read_file 工具（path 为上面的相对路径），"
                    "拿到内容后再回答；不要凭文件名猜测内容。\n"
                    "写入文件必须走两步确认流程：\n"
                    "第一步：需要写入或修改文件时，先回复以 [写入提案] 开头的提案——说明目标文件、"
                    "修改理由和打算怎么改。此时严禁调用 write_file/edit_file，也不要贴出完整新代码。\n"
                    "第二步：用户在界面点确认后，你会收到一条以 [确认写入] 开头的用户消息，"
                    "那时才调用 write_file/edit_file 生成内容并写入。"
                    "用户未确认前调用写入工具会被系统直接拒绝。"
                )
            else:
                trace["workspace_error"] = "目录不存在"
        items.append({"role": "user", "content": user_content})
        try:
            if cancelled():
                trace.update({"status": "cancelled", "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
                append_jsonl(self.settings.trace_file, trace)
                return "已取消当前思考，未发送任何内容。"
            answer = ""
            for _ in range(CHAT_TOOL_ROUNDS):
                result = self._completion(system_prompt, items, tools=tools, on_delta=on_delta, on_stage=on_stage)
                answer = result["content"]
                if not tools or not result["tool_calls"]:
                    break
                items.append({"role": "tool_calls", "content": answer, "calls": result["tool_calls"]})
                for call in result["tool_calls"]:
                    try:
                        arguments = json.loads(call["arguments"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                    if call["name"] == "get_system_info":
                        if on_stage:
                            on_stage("querying_server", "系统信息")
                        items.append({"role": "tool_result", "call_id": call["id"],
                                      "content": json.dumps(self.server.query(), ensure_ascii=False)})
                    elif call["name"] == "get_services":
                        if on_stage:
                            on_stage("querying_server", "运行中的服务")
                        items.append({"role": "tool_result", "call_id": call["id"],
                                      "content": json.dumps(self.server.query_services(), ensure_ascii=False)})
                    elif call["name"] == "get_pm2_processes":
                        if on_stage:
                            on_stage("querying_server", "PM2 进程")
                        items.append({"role": "tool_result", "call_id": call["id"],
                                      "content": json.dumps(self.server.query_pm2(), ensure_ascii=False)})
                    elif call["name"] == "read_file" and workspace_root:
                        target_name = str(arguments.get("path", "")).strip() or "工作空间文件"
                        if on_stage:
                            on_stage("reading_file", target_name)
                        content = read_workspace_file(workspace_root, arguments.get("path"))
                        items.append({"role": "tool_result", "call_id": call["id"], "content": content})
                    elif call["name"] == "write_file" and workspace_root:
                        content = self._handle_write_file(arguments, items, workspace_root, trace)
                        items.append({"role": "tool_result", "call_id": call["id"], "content": content})
                    elif call["name"] == "edit_file" and workspace_root:
                        content = self._handle_edit_file(arguments, items, workspace_root, trace)
                        items.append({"role": "tool_result", "call_id": call["id"], "content": content})
                    else:
                        items.append({"role": "tool_result", "call_id": call["id"], "content": f"错误：不支持的工具 {call['name']}"})
            else:
                answer = answer or "连续多轮读取文件仍未得到结论，已停止。请缩小问题范围再试。"
            answer = answer or "模型没有返回文本。"
            trace.update({
                "status": "ok",
                "tool_rounds": trace.get("tool_rounds", 0),
                "ttfb_ms": result.get("ttfb_ms") if isinstance(result, dict) else None,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            })
            append_jsonl(self.settings.trace_file, trace)
            return answer
        except Exception as error:
            trace.update({"status": "error", "error": str(error), "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
            append_jsonl(self.settings.trace_file, trace)
            return f"请求失败：{friendly_model_error(error)}"

    def run_turn(self, question: str, is_cancelled: Optional[Callable[[], bool]] = None, on_delta: Optional[Callable[[str], None]] = None, on_stage: Optional[Callable[[str, str], None]] = None) -> str:
        """服务器模块：function calling 工具注册表 + 正则兜底。

        快路径：用户说"重启 xx"这类明确 PM2 指令，正则直接命中，跳过首次模型调用。
        常规路径：带 SERVER_TOOLS 走 function calling，最多 SERVER_TOOL_ROUNDS 轮；
        模型不调工具就直接当普通问题回答。
        """
        started = time.perf_counter()
        cancelled = is_cancelled or (lambda: False)
        trace: Dict[str, Any] = {"question_length": len(question)}
        try:
            hint = infer_pm2_plan(question)
            if isinstance(hint, dict) and hint.get("action") in WRITE_ACTIONS:
                decision = validate_plan(hint)
                trace.update({"action": decision.action, "permission": decision.permission, "allowed": decision.allowed, "path": "hint"})
                answer = self._run_single_action(question, decision, trace, cancelled, on_delta, on_stage)
                self.memory.update(decision.action, decision.plan, answer, question)
                append_jsonl(self.settings.trace_file, trace)
                return answer

            items: list = [{"role": "user", "content": question}]
            tools = to_chat_tool_schemas()
            decisions: list = []
            answer = ""
            for _ in range(SERVER_TOOL_ROUNDS):
                result = self._completion(f"{time_context()}\n\n{TURN_SYSTEM_PROMPT}", items, tools=tools, on_delta=on_delta, on_stage=on_stage)
                answer = result["content"]
                if not result["tool_calls"]:
                    break
                items.append({"role": "tool_calls", "content": answer, "calls": result["tool_calls"]})
                for call in result["tool_calls"]:
                    decision, tool_result = self._run_tool_call(call, trace)
                    decisions.append(decision)
                    items.append({"role": "tool_result", "call_id": call["id"], "content": json.dumps(tool_result, ensure_ascii=False)})
                if cancelled():
                    trace.update({"status": "cancelled", "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
                    append_jsonl(self.settings.trace_file, trace)
                    return "已取消当前思考，未执行服务器操作。"
            else:
                answer = answer or "多轮工具调用仍未得到结论，已停止。请缩小问题范围再试。"
            answer = answer or "模型没有返回文本。"

            last_decision = decisions[-1] if decisions else None
            if last_decision is not None:
                self.memory.update(last_decision.action, last_decision.plan, answer, question)
            for decision in decisions:
                if decision.action in WRITE_ACTIONS:
                    append_jsonl(self.settings.audit_file, {"action": decision.action, "plan": decision.plan, "status": "executed"})
            trace.update({"status": "ok", "path": "tools", "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
            append_jsonl(self.settings.trace_file, trace)
            return answer
        except Exception as error:
            trace.update({"status": "error", "error": str(error), "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
            append_jsonl(self.settings.trace_file, trace)
            return f"请求失败：{friendly_model_error(error)}"

    def _run_single_action(self, question: str, decision: Decision, trace: Dict[str, Any], cancelled: Callable[[], bool], on_delta, on_stage) -> str:
        """快路径与单动作执行：权限、确认、审计语义与旧版一致。"""
        started = time.perf_counter()
        if cancelled():
            trace.update({"status": "cancelled", "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
            return "已取消当前思考，未执行服务器操作。"
        if not decision.allowed:
            result = {"ok": False, "blocked": True, "message": decision.reason}
        else:
            result = self._execute(decision)
        if cancelled():
            trace.update({"status": "cancelled"})
            return "已取消当前思考，未执行服务器操作。"
        if decision.action in WRITE_ACTIONS:
            append_jsonl(self.settings.audit_file, {
                "action": decision.action,
                "plan": decision.plan,
                "status": "cancelled" if result.get("cancelled") else "executed" if result.get("ok") else "failed",
            })
        answer = self._answer(question, result, on_delta=on_delta, on_stage=on_stage)
        trace.update({"status": "ok", "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
        return answer

    def _run_tool_call(self, call: Dict[str, Any], trace: Dict[str, Any]) -> tuple:
        """执行一次模型发起的工具调用，返回 (Decision, 结果 dict)。"""
        spec = SERVER_TOOLS_BY_NAME.get(call.get("name", ""))
        if spec is None:
            decision = Decision(False, "unknown", "BLOCKED", {}, f"不支持的工具 {call.get('name')}")
            return decision, {"ok": False, "blocked": True, "message": f"不支持的工具 {call.get('name')}"}
        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        if spec.action == "pm2_dynamic":
            operation = str(arguments.get("operation", "")).strip().lower()
            action = OPERATION_TO_ACTION.get(operation)
            if action is None:
                decision = Decision(False, "unknown", "BLOCKED", {}, f"未知的 PM2 操作 {operation}")
                return decision, {"ok": False, "blocked": True, "message": f"operation 只能是 start/stop/restart，收到 {operation!r}"}
            plan = {"action": action, "name": str(arguments.get("name", "")).strip() or None}
        else:
            action, plan = spec.action, {"action": spec.action}
        decision = validate_plan(plan)
        trace.update({"action": decision.action, "permission": decision.permission, "allowed": decision.allowed})
        if not decision.allowed:
            return decision, {"ok": False, "blocked": True, "message": decision.reason}
        result = self._execute(decision)
        return decision, result

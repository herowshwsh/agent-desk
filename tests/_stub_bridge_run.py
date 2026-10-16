"""离线端到端验证的bridge 启动器：stub 掉 OpenAI 客户端与 SSH 客户端。

不发真实网络请求，也不连真实服务器，只验证模块分发、history 传递、
trace 字段与审计隔离。

三条模型调用路径用不同 system 提示区分，返回内容里带 MODE 标记：
  路由器（agent 模块）      -> 合法 JSON 计划
  工作流结果总结（agent 模块） -> MODE=agent
  模块 system prompt（chat）  -> MODE=chat
"""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _resp(content: str):
    message = type("Msg", (), {"content": content})()
    return type("Resp", (), {"choices": [type("Choice", (), {"message": message})()]})()


class StubCompletions:
    def __init__(self, recorder: dict) -> None:
        self.recorder = recorder

    def create(self, *, model, messages, **kwargs):
        system = next((item.get("content", "") for item in messages if item.get("role") == "system"), "")
        recorder = self.recorder
        recorder["model"] = model
        recorder["call_count"] = recorder.get("call_count", 0) + 1
        recorder["last_system"] = system[:80]
        if "路由器" in system:
            recorder["path"] = "router"
            return _stream_or_not(kwargs, '{"action": "server_info", "name": null}')
        if "只根据工作流结果回答" in system:
            recorder["path"] = "agent_answer"
            recorder["history_len"] = len(messages) - 2
            return _stream_or_not(kwargs, "MODE=agent")
        # 工具注册表流程：第一轮带 tools 且还没有工具结果 → 返回 get_system_info 调用；
        # 已有工具结果 → 总结轮。
        has_tool_result = any(m.get("role") in ("tool", "tool_calls", "tool_result") for m in messages)
        if kwargs.get("tools") and not has_tool_result:
            recorder["path"] = "router"
            return _tool_call_stream()
        if has_tool_result:
            recorder["path"] = "agent_answer"
            return _stream_or_not(kwargs, "MODE=agent")
        recorder["path"] = "chat"
        # chat 路径：system 是模块自己的提示，之后是历史 + 当前问题。
        recorder["history_len"] = len(messages) - 2
        recorder["chat_system"] = system
        return _stream_or_not(kwargs, f"MODE={recorder['path']}")


def _stream_or_not(kwargs, text: str):
    """bridge 现在按需走流式：stream=True 时返回 SSE 风格的 chunk 迭代器。"""
    if not kwargs.get("stream"):
        return _resp(text)

    class Delta:
        def __init__(self, content: str) -> None:
            self.content = content
            self.tool_calls = None

    class Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [type("Choice", (), {"delta": Delta(content)})()]

    # 分三段吐出，模拟真实流式
    third = max(1, len(text) // 3)
    parts = [text[:third], text[third:third * 2], text[third * 2:]]
    return iter([Chunk(part) for part in parts if part])


def _tool_call_stream():
    """模拟第一轮模型发起 get_system_info 工具调用的流式分片。"""

    class ToolDelta:
        def __init__(self) -> None:
            self.index = 0
            self.id = "call-stub-1"
            self.function = type("F", (), {"name": "get_system_info", "arguments": "{}"})()

    class Delta:
        def __init__(self) -> None:
            self.content = None
            self.reasoning_content = None
            self.reasoning = None
            self.tool_calls = [ToolDelta()]

    class Chunk:
        def __init__(self) -> None:
            self.choices = [type("Choice", (), {"delta": Delta()})()]

    return iter([Chunk()])


class StubServerClient:
    """完全不建立 SSH 连接。"""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args

    def query(self):
        return {"ok": True, "hostname": "stub-host", "note": "离线验证，未连接真实服务器"}

    def query_services(self):
        return {"ok": True, "services": []}

    def query_pm2(self):
        return {"ok": True, "processes": []}

    def manage_pm2(self, action, name):
        return {"ok": True, "action": action, "name": name}


def install_stubs() -> dict:
    recorder: dict = {}

    class RecordingOpenAI:
        def __init__(self, **options) -> None:
            self.options = options
            self.chat = type("Chat", (), {"completions": StubCompletions(recorder)})()

    import agentdesk_core.agent as agent_module
    import agentdesk_core.server_client as server_module

    agent_module.OpenAI = RecordingOpenAI
    server_module.ServerClient = StubServerClient
    # agent.py 在import 时已绑定名字，需要一起替换。
    if hasattr(agent_module, "ServerClient"):
        agent_module.ServerClient = StubServerClient
    return recorder


def main() -> None:
    os.environ.setdefault("AGENT_TRACE_FILE", str(ROOT / "agentdesk_trace.jsonl"))
    os.environ.setdefault("AGENT_AUDIT_FILE", str(ROOT / "agentdesk_audit.jsonl"))
    install_stubs()

    from run_agentdesk_bridge import DesktopBridge

    try:
        bridge = DesktopBridge()
    except Exception as error:  # noqa: BLE001 - 与真实 bridge 保持一致
        print(json.dumps({"type": "bridge_error", "message": str(error)}, ensure_ascii=False), flush=True)
        return

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            bridge.handle(json.loads(line))
        except json.JSONDecodeError as error:
            print(json.dumps({"type": "bridge_error", "message": str(error)}, ensure_ascii=False), flush=True)
    # stdin EOF 后先等工作线程 emit，再关闭，否则请求会被当作取消。
    time.sleep(1.5)
    bridge.close()


if __name__ == "__main__":
    main()
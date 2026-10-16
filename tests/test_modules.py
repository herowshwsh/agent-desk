import json
import tempfile  # noqa: F401  - 保留给不依赖探测的环境


class _ProjectTmpDir(str):
    """沙箱环境的文件删除被拦截时，系统 tempfile 探测会全部失败。
    改用项目内固定目录：创建必成功，删除尽力而为（残留由 git 忽略）。
    继承 str：Path(obj) 与 with temp_directory() as d 两种用法都兼容。"""

    def __new__(cls):
        import uuid

        base = Path(__file__).resolve().parent / "_tmp"
        path = base / uuid.uuid4().hex[:10]
        path.mkdir(parents=True, exist_ok=True)
        obj = super().__new__(cls, str(path))
        obj._path = path
        return obj

    def __enter__(self):
        return str(self)

    def __exit__(self, *exc):
        import shutil

        try:
            shutil.rmtree(self._path)
        except OSError:
            pass
        return False

    def cleanup(self):
        self.__exit__(None, None, None)

    @property
    def name(self):
        return str(self)


def temp_directory():
    return _ProjectTmpDir()

import contextlib
import io
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentdesk_core.agent import CHAT_HISTORY_LIMIT, _history_messages
from run_agentdesk_bridge import DesktopBridge
from agentdesk_core.server_store import read_server_catalog
from agentdesk_core.server_store import read_server_catalog
from agentdesk_core.config import load_settings, module_by_id
from agentdesk_core.model_store import mask_key, read_model_catalog, validate_models, write_model_catalog
from agentdesk_core.server_store import public_servers, validate_servers, write_server_catalog

SERVER_ONLY = {"id": "server", "label": "服务器助手", "mode": "agent"}
CHAT_ONLY = {"id": "chat", "label": "自由聊天", "mode": "chat"}


@contextmanager
def settings_from(payload, models=None):
    """用临时目录构造 Settings，完全不依赖本机 models.json / servers.json。"""
    with temp_directory() as directory:
        root = Path(directory)
        modules_path = root / "modules.json"
        modules_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        models_path = root / "models.json"
        model_entries = models if models is not None else [
            {"id": "m1", "label": "M1", "model": "m1", "api_key": "sk-test"},
        ]
        models_path.write_text(
            json.dumps({"default": model_entries[0]["id"], "models": model_entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        servers_path = root / "servers.json"
        servers_path.write_text(
            json.dumps({"default": "s1", "servers": [
                {"id": "s1", "label": "S1", "host": "127.0.0.1", "port": 22,
                 "username": "root", "password": "pw", "connect_timeout": 1},
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )
        env = {
            # 显式指定测试配置，避免读本机真实配置
            "AGENT_MODEL_ID": model_entries[0]["id"],
            "AGENT_MODEL_CONFIG": str(models_path),
            "AGENT_MODULES_CONFIG": str(modules_path),
            "AGENT_SERVERS_CONFIG": str(servers_path),
        }
        with patch.dict("os.environ", env, clear=False):
            yield load_settings()


class ModuleConfigTests(unittest.TestCase):
    def test_modules_are_loaded_in_order(self):
        with settings_from({"default": "chat", "modules": [SERVER_ONLY, CHAT_ONLY]}) as settings:
            self.assertEqual([module.id for module in settings.modules], ["server", "chat"])
            self.assertEqual(settings.default_module_id, "chat")

    def test_show_trace_defaults_by_mode(self):
        with settings_from({"modules": [CHAT_ONLY]}) as settings:
            self.assertFalse(settings.modules[0].show_trace)
        with settings_from({"modules": [SERVER_ONLY]}) as settings:
            self.assertTrue(settings.modules[0].show_trace)

    def test_prompts_are_normalized(self):
        payload = {"modules": [{"id": "chat", "label": "聊天", "mode": "chat", "prompts": ["有效", "", 123, None, "  有效二  "]}]}
        with settings_from(payload) as settings:
            self.assertEqual(settings.modules[0].prompts, ("有效", "有效二"))

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(RuntimeError):
            with settings_from({"modules": [{"id": "x", "label": "X", "mode": "shell"}]}):
                pass

    def test_duplicate_module_id_is_rejected(self):
        payload = {"modules": [CHAT_ONLY, {"id": "chat", "label": "另一个", "mode": "chat"}]}
        with self.assertRaises(RuntimeError):
            with settings_from(payload):
                pass

    def test_module_without_id_is_rejected(self):
        with self.assertRaises(RuntimeError):
            with settings_from({"modules": [{"label": "无 id", "mode": "chat"}]}):
                pass

    def test_empty_module_list_is_rejected(self):
        with self.assertRaises(RuntimeError):
            with settings_from({"modules": []}):
                pass

    def test_unknown_default_falls_back_to_first(self):
        with settings_from({"default": "missing", "modules": [CHAT_ONLY]}) as settings:
            self.assertEqual(settings.default_module_id, "chat")

    def test_module_by_id_falls_back_to_default(self):
        payload = {"default": "server", "modules": [SERVER_ONLY, CHAT_ONLY]}
        with settings_from(payload) as settings:
            self.assertEqual(module_by_id(settings, "chat").id, "chat")
            self.assertEqual(module_by_id(settings, "").id, "server")
            self.assertEqual(module_by_id(settings, "nope").id, "server")


class ProjectModuleFileTests(unittest.TestCase):
    """仓库自带的 modules.json 必须始终可用（不依赖本机 models.json）。"""

    def setUp(self):
        from agentdesk_core.config import _load_modules, PROJECT_ROOT

        modules, default_id = _load_modules(PROJECT_ROOT / "modules.json")
        self.modules = modules
        self.default_id = default_id

    @property
    def settings(self):
        class _Stub:
            pass

        stub = _Stub()
        stub.modules = self.modules
        stub.default_module_id = self.default_id
        return stub

    def test_both_modules_present(self):
        ids = {module.id for module in self.settings.modules}
        self.assertIn("server", ids)
        self.assertIn("chat", ids)

    def test_default_module_is_server(self):
        self.assertEqual(self.settings.default_module_id, "server")

    def test_chat_module_has_no_trace(self):
        chat = next(module for module in self.settings.modules if module.id == "chat")
        self.assertEqual(chat.mode, "chat")
        self.assertFalse(chat.show_trace)
        self.assertTrue(chat.system_prompt)

    def test_every_module_has_ui_copy(self):
        for module in self.settings.modules:
            self.assertTrue(module.label, f"{module.id} 缺少 label")
            self.assertTrue(module.placeholder, f"{module.id} 缺少 placeholder")


class FriendlyErrorTests(unittest.TestCase):
    """第三方返回什么都要翻译成人话，不能把裸异常甩给用户。"""

    def _translate(self, kind, message):
        from agentdesk_core.agent import friendly_model_error

        error = type(kind, (Exception,), {})(message)
        return friendly_model_error(error)

    def test_timeout(self):
        text = self._translate("APITimeoutError", "Request timed out: 45 seconds")
        self.assertIn("响应超时", text)
        self.assertIn("APITimeoutError", text)

    def test_connection(self):
        text = self._translate("APIConnectionError", "Connection error, retrying")
        self.assertIn("无法连接模型服务", text)

    def test_auth(self):
        text = self._translate("AuthenticationError", "Error code: 401 - invalid api key")
        self.assertIn("密钥无效", text)

    def test_rate_limit(self):
        text = self._translate("RateLimitError", "Error code: 429 - rate limit exceeded")
        self.assertIn("限流", text)

    def test_model_not_found(self):
        text = self._translate("NotFoundError", "Error code: 404 - model not exist")
        self.assertIn("模型名称不存在", text)

    def test_server_error(self):
        text = self._translate("InternalServerError", "Error code: 500")
        self.assertIn("服务端出错", text)

    def test_unknown_still_has_reason(self):
        text = self._translate("WeirdError", "一些奇怪的东西")
        self.assertIn("返回异常", text)
        self.assertIn("WeirdError", text)

    def test_message_is_capped(self):
        text = self._translate("BoomError", "x" * 5000)
        self.assertLess(len(text), 260)


class WorkspaceTests(unittest.TestCase):
    """工作空间文件树与 read_file 工具的安全边界。"""

    def setUp(self):

        self._tmp = temp_directory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
        (self.root / "README.md").write_text("# demo", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "x.js").write_text("var x", encoding="utf-8")
        (self.root / ".hidden").write_text("secret", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_tree_lists_files_skips_noise(self):
        from agentdesk_core.attachments import build_file_tree

        tree = build_file_tree(self.root)
        self.assertIn("src/main.py", tree)
        self.assertIn("README.md", tree)
        self.assertNotIn("node_modules", tree)
        self.assertNotIn(".hidden", tree)

    def test_read_inside_workspace(self):
        from agentdesk_core.attachments import read_workspace_file

        self.assertEqual(read_workspace_file(self.root, "src/main.py"), "print('hello')")
        self.assertEqual(read_workspace_file(self.root, "README.md"), "# demo")

    def test_read_outside_is_rejected(self):
        from agentdesk_core.attachments import read_workspace_file

        text = read_workspace_file(self.root, "../outside.txt")
        self.assertIn("不在工作空间内", text)
        text = read_workspace_file(self.root, "/etc/passwd")
        self.assertIn("不在工作空间内", text)

    def test_read_missing_and_directory(self):
        from agentdesk_core.attachments import read_workspace_file

        self.assertIn("不存在", read_workspace_file(self.root, "missing.txt"))
        self.assertIn("只能读文件", read_workspace_file(self.root, "src"))

    def test_read_requires_path(self):
        from agentdesk_core.attachments import read_workspace_file

        self.assertIn("缺少 path", read_workspace_file(self.root, ""))

    def test_inside_workspace_symlink_escape(self):
        import os

        from agentdesk_core.attachments import _inside_workspace

        with temp_directory() as outside:
            link = self.root / "link"
            os.symlink(outside, link)
            self.assertFalse(_inside_workspace(self.root, link / "secret.txt"))


class WorkspaceChatLoopTests(unittest.TestCase):
    """带工作空间时模型可通过 read_file 工具读文件，形成两轮调用。"""

    def test_run_chat_reads_file_via_tool(self):

        from agentdesk_core.agent import AgentDesk
        from agentdesk_core.config import ModuleSpec

        with temp_directory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("魔力数字是 42", encoding="utf-8")
            spec = ModuleSpec(
                id="chat", label="自由聊天", hint="", icon="chat", mode="chat",
                show_trace=False, show_sessions=True, model_locked=False, allow_files=True,
                welcome_title="", welcome_subtitle="", placeholder="",
                prompts=(), system_prompt="你是助手。",
            )
            settings = MagicMock(
                model_api_key="k", model_base_url="", model_name="m1",
                trace_file=root / "trace.jsonl", audit_file=root / "audit.jsonl",
                state_file=root / "state.json",
                server_host="", server_username="", server_password="",
                server_port=22, server_connect_timeout=5, request_timeout=45,
            )
            agent = AgentDesk(settings)

            calls = {"count": 0}

            class Msg:
                def __init__(self, content, tool_calls=None):
                    self.content = content
                    self.tool_calls = tool_calls

            class ToolCall:
                def __init__(self):
                    self.id = "call-1"
                    self.function = type("F", (), {"name": "read_file", "arguments": '{"path": "note.txt"}'})()

            class Choice:
                def __init__(self, message):
                    self.message = message

            class Resp:
                def __init__(self, message):
                    self.choices = [Choice(message)]

            class Completions:
                def create(self, *, model, messages, tools=None, **kwargs):
                    calls["count"] += 1
                    if calls["count"] == 1:
                        assert tools, "第一轮就应该带 tools"
                        user_text = messages[-1]["content"]
                        assert "note.txt" in user_text, "文件树应包含在用户消息里"
                        tc = ToolCall()
                        return Resp(Msg("", [tc]))
                    tool_result = messages[-1]["content"]
                    assert "魔力数字是 42" in tool_result, "工具结果应回填文件内容"
                    return Resp(Msg("数字是 42"))

            agent.client.chat.completions = Completions()
            answer = agent.run_chat("note.txt 里写了什么", spec, workspace=str(root))
            self.assertEqual(answer, "数字是 42")
            self.assertEqual(calls["count"], 2)

    def test_run_chat_without_workspace_stays_single_turn(self):
        from agentdesk_core.agent import AgentDesk
        from agentdesk_core.config import ModuleSpec

        spec = ModuleSpec(
            id="chat", label="自由聊天", hint="", icon="chat", mode="chat",
            show_trace=False, show_sessions=True, model_locked=False, allow_files=True,
            welcome_title="", welcome_subtitle="", placeholder="",
            prompts=(), system_prompt="",
        )
        settings = MagicMock(
            model_api_key="k", model_base_url="", model_name="m1",
            trace_file=Path("trace.jsonl"), audit_file=Path("audit.jsonl"),
            state_file=Path("state.json"),
            server_host="", server_username="", server_password="",
            server_port=22, server_connect_timeout=5, request_timeout=45,
        )
        agent = AgentDesk(settings)
        calls = {"count": 0}

        class Msg:
            content = "直接回答"
            tool_calls = None

        class Resp:
            choices = [type("C", (), {"message": Msg()})()]

        class Completions:
            def create(self, **kwargs):
                calls["count"] += 1
                assert not kwargs.get("tools"), "没有工作空间就不该带 tools"
                return Resp()

        agent.client.chat.completions = Completions()
        answer = agent.run_chat("你好", spec)
        self.assertEqual(answer, "直接回答")
        self.assertEqual(calls["count"], 1)


class ResponsesApiTests(unittest.TestCase):
    """wire_api=responses 时的协议转换与调用。"""

    def _agent(self, **overrides):
        from agentdesk_core.agent import AgentDesk

        settings = MagicMock(
            model_api_key="k", model_base_url="", model_name="gpt-5.6-sol",
            model_wire_api=overrides.get("wire_api", "responses"),
            model_reasoning_effort=overrides.get("reasoning_effort", "high"),
            model_store=overrides.get("store", False),
            trace_file=Path("trace.jsonl"), audit_file=Path("audit.jsonl"),
            state_file=Path("state.json"),
            server_host="", server_username="", server_password="",
            server_port=22, server_connect_timeout=5, request_timeout=45,
        )
        return AgentDesk(settings)

    def test_config_reads_wire_api(self):
        """wire_api 从配置读取（用临时配置，不依赖本机 models.json）。"""
        with settings_from({"modules": [SERVER_ONLY]}, models=[
            {"id": "r1", "label": "R1", "model": "gpt-x", "base_url": "https://relay.example.com",
             "api_key": "sk-test", "wire_api": "responses", "reasoning_effort": "high"},
        ]) as settings:
            self.assertEqual(settings.model_wire_api, "responses")
            self.assertEqual(settings.model_reasoning_effort, "high")

    def test_responses_uses_responses_endpoint(self):
        agent = self._agent()
        captured = {}

        class Resp:
            output_text = "你好"
            output = []

        agent.client.responses = type("R", (), {"create": lambda self, **kw: captured.update(kw) or Resp()})()
        result = agent._completion(
            "你是助手",
            [{"role": "user", "content": "你好"}],
        )
        self.assertEqual(result["content"], "你好")
        self.assertEqual(captured["model"], "gpt-5.6-sol")
        self.assertFalse(captured["store"], "disable_response_storage 应映射为 store=False")
        self.assertEqual(captured["reasoning"], {"effort": "high"})
        self.assertEqual(captured["instructions"], "你是助手")
        self.assertEqual(captured["input"], [{"role": "user", "content": "你好"}])
        self.assertNotIn("messages", captured)

    def test_responses_tool_round_trip(self):
        agent = self._agent()
        contents_seen = []

        class Resp:
            output_text = "文件内容是 abc"
            output = []

        class Responses:
            def create(self, **kwargs):
                contents_seen.append(list(kwargs["input"]))
                return Resp()

        agent.client.responses = Responses()
        result = agent._completion(
            "你是助手",
            [
                {"role": "user", "content": "看下 a.txt"},
                {"role": "tool_calls", "content": "", "calls": [{"id": "call-9", "name": "read_file", "arguments": '{"path": "a.txt"}'}]},
                {"role": "tool_result", "call_id": "call-9", "content": "abc"},
            ],
            tools=[{"type": "function", "function": {"name": "read_file", "description": "d", "parameters": {}}}],
        )
        self.assertEqual(result["content"], "文件内容是 abc")
        first = contents_seen[0]
        self.assertTrue(any(item.get("type") == "function_call" for item in first), "tool_calls 应转成 function_call")
        self.assertTrue(any(item.get("type") == "function_call_output" for item in first), "tool_result 应转成 function_call_output")

    def test_responses_tools_format_is_flat(self):
        agent = self._agent()
        captured = {}

        class Resp:
            output_text = "ok"
            output = []

        agent.client.responses = type("R", (), {"create": lambda self, **kw: captured.update(kw) or Resp()})()
        agent._completion(
            "s",
            [{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "read_file", "description": "d", "parameters": {"type": "object"}}}],
        )
        self.assertEqual(captured["tools"][0]["type"], "function")
        self.assertEqual(captured["tools"][0]["name"], "read_file")
        self.assertNotIn("function", captured["tools"][0], "responses 的工具是扁平结构，不嵌套 function")

    def test_chat_wire_api_still_works(self):
        agent = self._agent(wire_api="chat", reasoning_effort="", store=True)
        captured = {}

        class Msg:
            content = "plain"
            tool_calls = None

        class Resp:
            choices = [type("C", (), {"message": Msg()})()]

        agent.client.chat = type("Chat", (), {"completions": type("C", (), {"create": lambda self, **kw: captured.update(kw) or Resp()})()})()
        result = agent._completion("s", [{"role": "user", "content": "x"}])
        self.assertEqual(result["content"], "plain")
        self.assertIn("messages", captured)
        self.assertNotIn("store", captured)


class StreamingTests(unittest.TestCase):
    """流式输出：delta 逐段回调，tool_call 从分片里拼回。"""

    def _agent(self, wire_api="chat"):
        from agentdesk_core.agent import AgentDesk

        settings = MagicMock(
            model_api_key="k", model_base_url="", model_name="m1",
            model_wire_api=wire_api, model_reasoning_effort="", model_store=True,
            trace_file=Path("trace.jsonl"), audit_file=Path("audit.jsonl"),
            state_file=Path("state.json"),
            server_host="", server_username="", server_password="",
            server_port=22, server_connect_timeout=5, request_timeout=45,
        )
        return AgentDesk(settings)

    def test_chat_stream_emits_deltas(self):
        agent = self._agent("chat")

        class Delta:
            def __init__(self, content=None, tool_calls=None):
                self.content = content
                self.tool_calls = tool_calls

        class Chunk:
            def __init__(self, delta):
                self.choices = [type("C", (), {"delta": delta})()]

        chunks = [
            Chunk(Delta(content="你")),
            Chunk(Delta(content="好")),
            Chunk(Delta(content="！")),
        ]
        captured = {}
        agent.client.chat = type("Chat", (), {"completions": type("C", (), {"create": staticmethod(lambda **kw: captured.update(kw) or iter(chunks))})()})()
        received = []
        result = agent._completion("s", [{"role": "user", "content": "x"}], on_delta=received.append)
        self.assertTrue(captured.get("stream") is True, "传 on_delta 时应走流式")
        self.assertEqual(received, ["你", "好", "！"])
        self.assertEqual(result["content"], "你好！")
        self.assertEqual(result["tool_calls"], [])

    def test_chat_stream_reassembles_tool_calls(self):
        agent = self._agent("chat")

        class Delta:
            def __init__(self, content=None, tool_calls=None):
                self.content = content
                self.tool_calls = tool_calls

        class ToolDelta:
            def __init__(self, index, id=None, name=None, arguments=None):
                self.index = index
                self.id = id
                self.function = type("F", (), {"name": name, "arguments": arguments})()

        class Chunk:
            def __init__(self, delta):
                self.choices = [type("C", (), {"delta": delta})()]

        chunks = [
            Chunk(Delta(tool_calls=[ToolDelta(0, id="c1", name="read_file")])),
            Chunk(Delta(tool_calls=[ToolDelta(0, arguments='{"path"')])),
            Chunk(Delta(tool_calls=[ToolDelta(0, arguments=': "a.txt"}')])),
        ]
        agent.client.chat = type("Chat", (), {"completions": type("C", (), {"create": staticmethod(lambda **kw: iter(chunks))})()})()
        result = agent._completion("s", [{"role": "user", "content": "x"}], on_delta=lambda _: None, tools=[{"type": "function", "function": {"name": "read_file"}}])
        self.assertEqual(result["tool_calls"], [{"id": "c1", "name": "read_file", "arguments": '{"path": "a.txt"}'}])

    def test_responses_stream_emits_deltas(self):
        agent = self._agent("responses")

        class Event:
            def __init__(self, type, **kw):
                self.type = type
                for key, value in kw.items():
                    setattr(self, key, value)

        events = [
            Event("response.output_text.delta", delta="你"),
            Event("response.output_text.delta", delta="好"),
        ]
        captured = {}
        agent.client.responses = type("R", (), {"create": lambda self, **kw: captured.update(kw) or iter(events)})()
        received = []
        result = agent._completion("s", [{"role": "user", "content": "x"}], on_delta=received.append)
        self.assertEqual(received, ["你", "好"])
        self.assertEqual(result["content"], "你好")
        self.assertTrue(captured.get("stream"))

    def test_responses_stream_reassembles_tool_calls(self):
        agent = self._agent("responses")

        class Event:
            def __init__(self, type, **kw):
                self.type = type
                for key, value in kw.items():
                    setattr(self, key, value)

        class Item:
            type = "function_call"
            call_id = "call-7"
            name = "read_file"
            arguments = ""

        events = [
            Event("response.output_item.added", output_index=0, item=Item()),
            Event("response.function_call_arguments.delta", output_index=0, delta='{"path"'),
            Event("response.function_call_arguments.delta", output_index=0, delta=': "a.txt"}'),
        ]
        agent.client.responses = type("R", (), {"create": lambda self, **kw: iter(events)})()
        result = agent._completion("s", [{"role": "user", "content": "x"}], on_delta=lambda _: None)
        self.assertEqual(result["tool_calls"], [{"id": "call-7", "name": "read_file", "arguments": '{"path": "a.txt"}'}])


    def test_chat_stream_stage_transitions(self):
        agent = self._agent("chat")

        class Delta:
            def __init__(self, content=None, reasoning_content=None):
                self.content = content
                self.reasoning_content = reasoning_content
                self.reasoning = None
                self.tool_calls = None

        class Chunk:
            def __init__(self, delta):
                self.choices = [type("C", (), {"delta": delta})()]

        chunks = [
            Chunk(Delta(reasoning_content="让我想想")),
            Chunk(Delta(reasoning_content="再想想")),
            Chunk(Delta(content="答案")),
        ]
        agent.client.chat = type("Chat", (), {"completions": type("C", (), {"create": staticmethod(lambda **kw: iter(chunks))})()})()
        stages = []
        agent._completion("s", [{"role": "user", "content": "x"}], on_delta=lambda _: None, on_stage=lambda stage, detail: stages.append(stage))
        # 去重后应为 connecting → thinking → generating
        deduped = [s for i, s in enumerate(stages) if i == 0 or s != stages[i - 1]]
        self.assertEqual(deduped, ["connecting", "thinking", "generating"], stages)

    def test_responses_stream_stage_transitions(self):
        agent = self._agent("responses")

        class Event:
            def __init__(self, type, **kw):
                self.type = type
                for key, value in kw.items():
                    setattr(self, key, value)

        events = [
            Event("response.created"),
            Event("response.reasoning_summary_text.delta", delta="思考"),
            Event("response.output_text.delta", delta="答案"),
        ]
        agent.client.responses = type("R", (), {"create": lambda self, **kw: iter(events)})()
        stages = []
        agent._completion("s", [{"role": "user", "content": "x"}], on_delta=lambda _: None, on_stage=lambda stage, detail: stages.append(stage))
        deduped = [s for i, s in enumerate(stages) if i == 0 or s != stages[i - 1]]
        self.assertEqual(deduped, ["connecting", "thinking", "generating"], stages)

    def test_run_chat_reports_reading_file_stage(self):

        from agentdesk_core.agent import AgentDesk
        from agentdesk_core.config import ModuleSpec

        with temp_directory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("内容", encoding="utf-8")
            spec = ModuleSpec(
                id="chat", label="自由聊天", hint="", icon="chat", mode="chat",
                show_trace=False, show_sessions=True, model_locked=False, allow_files=True,
                welcome_title="", welcome_subtitle="", placeholder="",
                prompts=(), system_prompt="",
            )
            settings = MagicMock(
                model_api_key="k", model_base_url="", model_name="m1",
                model_wire_api="chat", model_reasoning_effort="", model_store=True,
                trace_file=root / "trace.jsonl", audit_file=root / "audit.jsonl",
                state_file=root / "state.json",
                server_host="", server_username="", server_password="",
                server_port=22, server_connect_timeout=5, request_timeout=45,
            )
            agent = AgentDesk(settings)

            class Msg:
                def __init__(self, content, tool_calls=None):
                    self.content = content
                    self.tool_calls = tool_calls

            class ToolCall:
                def __init__(self):
                    self.id = "call-1"
                    self.function = type("F", (), {"name": "read_file", "arguments": '{"path": "note.txt"}'})()

            class Choice:
                def __init__(self, message):
                    self.message = message

            class Resp:
                def __init__(self, message):
                    self.choices = [Choice(message)]

            class Completions:
                def __init__(self):
                    self.round = 0

                def create(self, *, model, messages, **kwargs):
                    self.round += 1
                    if self.round == 1:
                        tc = ToolCall()
                        return Resp(Msg("", [tc]))
                    return Resp(Msg("读完了"))

            agent.client.chat.completions = Completions()
            stages = []
            answer = agent.run_chat("看下 note.txt", spec, workspace=str(root), on_stage=lambda stage, detail: stages.append((stage, detail)))
            self.assertEqual(answer, "读完了")
            self.assertIn(("reading_file", "note.txt"), stages, stages)


class RetryTests(unittest.TestCase):
    """瞬时错误自动重试一次；配错类错误不重试。"""

    def _agent(self):
        from agentdesk_core.agent import AgentDesk

        settings = MagicMock(
            model_api_key="k", model_base_url="", model_name="m1",
            model_wire_api="chat", model_reasoning_effort="", model_store=True,
            trace_file=Path("trace.jsonl"), audit_file=Path("audit.jsonl"),
            state_file=Path("state.json"),
            server_host="", server_username="", server_password="",
            server_port=22, server_connect_timeout=5, request_timeout=45,
        )
        return AgentDesk(settings)

    def test_retryable_error_retries_once(self):
        from agentdesk_core.agent import friendly_model_error, is_retryable_error

        error = type("APIConnectionError", (Exception,), {})("Connection error")
        self.assertTrue(is_retryable_error(error))
        self.assertTrue(is_retryable_error(type("RateLimitError", (Exception,), {})("Error code: 429")))
        self.assertFalse(is_retryable_error(type("AuthenticationError", (Exception,), {})("401 invalid key")))
        self.assertFalse(is_retryable_error(type("NotFoundError", (Exception,), {})("model not exist")))

    def test_completion_retries_and_succeeds(self):
        import agentdesk_core.agent as agent_module

        agent = self._agent()
        calls = {"count": 0}

        class Msg:
            content = "ok"
            tool_calls = None

        class Resp:
            choices = [type("C", (), {"message": Msg()})()]

        def flaky_create(**kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise type("APIConnectionError", (Exception,), {})("connection error")
            return Resp()

        agent.client.chat = type("Chat", (), {"completions": type("C", (), {"create": staticmethod(flaky_create)})()})()
        sleeps = []
        original_sleep = agent_module.time.sleep
        agent_module.time.sleep = lambda s: sleeps.append(s)
        try:
            result = agent._completion("s", [{"role": "user", "content": "x"}])
        finally:
            agent_module.time.sleep = original_sleep
        self.assertEqual(result["content"], "ok")
        self.assertEqual(calls["count"], 2, "失败后应重试一次")
        self.assertEqual(sleeps, [1.5])

    def test_auth_error_not_retried(self):
        agent = self._agent()
        calls = {"count": 0}

        def auth_fail(**kwargs):
            calls["count"] += 1
            raise type("AuthenticationError", (Exception,), {})("401 invalid api key")

        agent.client.chat = type("Chat", (), {"completions": type("C", (), {"create": staticmethod(auth_fail)})()})()
        with self.assertRaises(Exception):
            agent._completion("s", [{"role": "user", "content": "x"}])
        self.assertEqual(calls["count"], 1, "密钥错误不应重试")


class EncodingTests(unittest.TestCase):
    """中文环境常见编码：GBK / UTF-8 BOM / UTF-16 都能读。"""

    def test_decode_variants(self):

        from agentdesk_core.attachments import decode_text, read_file_content

        with temp_directory() as directory:
            root = Path(directory)
            cases = {
                "utf8.txt": ("中文内容".encode("utf-8"), "utf-8"),
                "gbk.txt": ("中文内容".encode("gbk"), "gbk"),
                "bom.txt": (b"\xef\xbb\xbf" + "中文内容".encode("utf-8"), "utf-8-sig"),
                "utf16.txt": ("中文内容".encode("utf-16"), "utf-16"),
            }
            for name, (data, _) in cases.items():
                path = root / name
                path.write_bytes(data)
                content, status = read_file_content(path)
                self.assertEqual(status, "ok", name)
                self.assertEqual(content, "中文内容", f"{name} 解码错误: {content!r}")
            self.assertEqual(decode_text("plain".encode("utf-8")), "plain")


class ToolRegistryTests(unittest.TestCase):
    """run_turn 的工具注册表流程：正则快路径 + function calling。"""

    def _agent(self, recorder):
        from agentdesk_core.agent import AgentDesk

        settings = MagicMock(
            model_api_key="k", model_base_url="", model_name="m1",
            model_wire_api="chat", model_reasoning_effort="", model_store=True,
            trace_file=Path("trace.jsonl"), audit_file=Path("audit.jsonl"),
            state_file=Path("state.json"),
            server_host="", server_username="", server_password="",
            server_port=22, server_connect_timeout=5, request_timeout=45,
        )
        agent = AgentDesk(settings)

        class Resp:
            def __init__(self, content):
                self.choices = [type("C", (), {"message": type("M", (), {"content": content, "tool_calls": None})()})()]

        agent.client.chat = type("Chat", (), {"completions": type("C", (), {"create": staticmethod(lambda **kw: recorder(kw) or Resp("好的")) })()})()
        return agent

    def test_hint_fast_path_skips_first_model_call(self):
        import agentdesk_core.agent as agent_module

        calls = {"count": 0}
        seen_tools = []

        def rec(kw):
            calls["count"] += 1
            seen_tools.append("tools" in kw)
            if calls["count"] > 1:
                # 快路径只应有总结轮；若带 tools 说明快路径没命中
                raise AssertionError("快路径不应出现带 tools 的调用")

        agent = self._agent(rec)
        answer = agent.run_turn("帮我重启 pm2 里的 app-server 进程")
        self.assertEqual(answer, "好的")
        self.assertEqual(calls["count"], 1, "正则命中写操作应跳过第一次模型调用，只做总结")
        self.assertEqual(seen_tools, [False], "总结轮不带 tools")

    def test_tool_call_round_executes_server_query(self):
        from agentdesk_core.agent import AgentDesk

        settings = MagicMock(
            model_api_key="k", model_base_url="", model_name="m1",
            model_wire_api="chat", model_reasoning_effort="", model_store=True,
            trace_file=Path("trace.jsonl"), audit_file=Path("audit.jsonl"),
            state_file=Path("state.json"),
            server_host="", server_username="", server_password="",
            server_port=22, server_connect_timeout=5, request_timeout=45,
        )
        agent = AgentDesk(settings)
        agent.server = MagicMock()
        agent.server.query.return_value = {"ok": True, "data": "host-info"}

        rounds = {"n": 0}

        class ToolCall:
            def __init__(self):
                self.id = "call-1"
                self.function = type("F", (), {"name": "get_system_info", "arguments": "{}"})()

        class Resp:
            def __init__(self, content, tool_calls):
                self.choices = [type("C", (), {"message": type("M", (), {"content": content, "tool_calls": tool_calls})()})()]

        def create(**kwargs):
            rounds["n"] += 1
            if rounds["n"] == 1:
                self.assertTrue(kwargs.get("tools"), "第一轮应带工具注册表")
                return Resp("", [ToolCall()])
            tool_result = kwargs["messages"][-1]["content"]
            self.assertIn("host-info", tool_result, "工具结果应回填")
            return Resp("服务器信息如下", None)

        agent.client.chat = type("Chat", (), {"completions": type("C", (), {"create": staticmethod(create)})()})()
        answer = agent.run_turn("看看服务器怎么样")
        self.assertEqual(answer, "服务器信息如下")
        self.assertEqual(rounds["n"], 2)

    def test_unknown_tool_returns_error_to_model(self):
        from agentdesk_core.agent import AgentDesk

        settings = MagicMock(
            model_api_key="k", model_base_url="", model_name="m1",
            model_wire_api="chat", model_reasoning_effort="", model_store=True,
            trace_file=Path("trace.jsonl"), audit_file=Path("audit.jsonl"),
            state_file=Path("state.json"),
            server_host="", server_username="", server_password="",
            server_port=22, server_connect_timeout=5, request_timeout=45,
        )
        agent = AgentDesk(settings)
        decision, result = agent._run_tool_call(
            {"name": "hack_server", "arguments": "{}", "id": "c1"}, {}
        )
        self.assertFalse(decision.allowed)
        self.assertIn("不支持的工具", result["message"])


APPROVED_ITEMS = [
    {"role": "assistant", "content": "[写入提案] 打算修改 note.txt"},
    {"role": "user", "content": "[确认写入] 同意该提案"},
]


APPROVED_ITEMS = [
    {"role": "assistant", "content": "[写入提案] 打算修改 note.txt"},
    {"role": "user", "content": "[确认写入] 同意该提案"},
]


class WorkspaceWriteTests(unittest.TestCase):
    """工作空间写入：确认后落盘+备份；拒绝则告知模型；越界拒绝。"""

    def _agent(self, approved: bool):
        from agentdesk_core.agent import AgentDesk

        settings = MagicMock(
            model_api_key="k", model_base_url="", model_name="m1",
            model_wire_api="chat", model_reasoning_effort="", model_store=True,
            trace_file=Path("trace.jsonl"), audit_file=Path("audit.jsonl"),
            state_file=Path("state.json"),
            server_host="", server_username="", server_password="",
            server_port=22, server_connect_timeout=5, request_timeout=45,
        )
        agent = AgentDesk(settings)
        agent.confirm = MagicMock(return_value=approved)
        return agent

    def _tool_call(self, arguments: str):
        from agentdesk_core.agent import AgentDesk

        return {"name": "write_file", "id": "call-w1", "arguments": arguments}

    def test_approved_write_creates_file_with_backup(self):
        import tempfile

        from agentdesk_core.attachments import read_workspace_file

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("旧内容", encoding="utf-8")
            agent = self._agent(approved=True)
            message = agent._handle_write_file(
                {"path": "note.txt", "content": "新内容"}, APPROVED_ITEMS, root, {}
            )
            self.assertIn("已写入", message)
            self.assertIn(".bak", message, "覆盖前应自动备份")
            self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "新内容")
            self.assertEqual(read_workspace_file(root, "note.txt"), "新内容")
            backups = list(root.glob("*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "旧内容")

    def test_rejected_write_does_not_touch_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("旧内容", encoding="utf-8")
            agent = self._agent(approved=False)
            message = agent._handle_write_file(
                {"path": "note.txt", "content": "新内容"}, [], root, {}
            )
            self.assertIn("还没有确认", message)
            self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "旧内容")
            self.assertFalse(list(root.glob("*.bak")))

    def test_path_escape_rejected_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = self._agent(approved=True)
            message = agent._handle_write_file(
                {"path": "../../outside.txt", "content": "越界"}, APPROVED_ITEMS, root, {}
            )
            self.assertIn("不在工作空间内", message)
            self.assertFalse((root.parent / "outside.txt").exists())

    def test_new_file_write_without_backup(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = self._agent(approved=True)
            message = agent._handle_write_file(
                {"path": "新建/hello.md", "content": "# 你好"}, APPROVED_ITEMS, root, {}
            )
            self.assertIn("已写入", message)
            self.assertNotIn("备份", message)
            self.assertEqual((root / "新建" / "hello.md").read_text(encoding="utf-8"), "# 你好")


class WorkspaceEditTests(unittest.TestCase):
    """补丁式编辑：唯一片段替换 + 备份；零次/多次匹配拒绝；越界不弹确认。"""

    def _agent(self, approved: bool):
        from agentdesk_core.agent import AgentDesk

        settings = MagicMock(
            model_api_key="k", model_base_url="", model_name="m1",
            model_wire_api="chat", model_reasoning_effort="", model_store=True,
            trace_file=Path("trace.jsonl"), audit_file=Path("audit.jsonl"),
            state_file=Path("state.json"),
            server_host="", server_username="", server_password="",
            server_port=22, server_connect_timeout=5, request_timeout=45,
        )
        agent = AgentDesk(settings)
        agent.confirm = MagicMock(return_value=approved)
        return agent

    def test_edit_replaces_unique_occurrence(self):
        import tempfile

        from agentdesk_core.attachments import read_workspace_file

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("timeout = 30\nname = 'a'\n", encoding="utf-8")
            agent = self._agent(approved=True)
            message = agent._handle_edit_file(
                {"path": "app.py", "old_text": "timeout = 30", "new_text": "timeout = 60"}, APPROVED_ITEMS, root, {}
            )
            self.assertIn("已编辑", message)
            self.assertIn("+1/-1", message)
            self.assertEqual(read_workspace_file(root, "app.py"), "timeout = 60\nname = 'a'\n")
            self.assertEqual(len(list(root.glob("*.bak"))), 1, "覆盖前应有备份")

    def test_edit_missing_fragment_rejected_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("timeout = 30\n", encoding="utf-8")
            agent = self._agent(approved=True)
            message = agent._handle_edit_file(
                {"path": "app.py", "old_text": "timeout = 999", "new_text": "x"}, APPROVED_ITEMS, root, {}
            )
            self.assertIn("没有找到", message)
            agent.confirm.assert_not_called(), "片段不存在不应打扰用户确认"
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "timeout = 30\n")

    def test_edit_ambiguous_fragment_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
            agent = self._agent(approved=True)
            message = agent._handle_edit_file(
                {"path": "app.py", "old_text": "x = 1", "new_text": "y = 2"}, APPROVED_ITEMS, root, {}
            )
            self.assertIn("出现了 2 次", message)
            agent.confirm.assert_not_called()

    def test_edit_path_escape_rejected_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = self._agent(approved=True)
            message = agent._handle_edit_file(
                {"path": "../evil.py", "old_text": "a", "new_text": "b"}, APPROVED_ITEMS, root, {}
            )
            self.assertIn("不在工作空间内", message)
            agent.confirm.assert_not_called()


class DiffTests(unittest.TestCase):
    """确认卡片的 diff 构建。"""

    def test_build_diff_marks_changes(self):
        from agentdesk_core.attachments import build_diff

        diff = build_diff("a\nb\nc\n", "a\nB\nc\n")
        self.assertIn("-b", diff)
        self.assertIn("+B", diff)
        self.assertIn("a", diff)

    def test_build_diff_no_change(self):
        from agentdesk_core.attachments import build_diff

        self.assertEqual(build_diff("same\n", "same\n"), "（内容没有变化）")


class BakFilterTests(unittest.TestCase):
    """写入备份不进文件树。"""

    def test_bak_files_hidden_from_tree(self):
        import tempfile

        from agentdesk_core.attachments import build_file_tree

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("x", encoding="utf-8")
            (root / "app.py.103000.bak").write_text("old", encoding="utf-8")
            tree = build_file_tree(root)
            self.assertIn("app.py", tree)
            self.assertNotIn(".bak", tree)


class HistoryMessageTests(unittest.TestCase):
    """模型历史裁剪：只留最近 N 条、忽略非法项。"""

    def test_keeps_recent_messages(self):
        history = [{"role": "user", "text": "一"}, {"role": "assistant", "text": "二"},
                   {"role": "user", "text": "三"}]
        messages = _history_messages(history)
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["role"], "user")

    def test_ignores_invalid_entries(self):
        history = [{"role": "user", "text": "ok"}, {"role": "system", "text": "忽略"},
                   {"role": "user"}, {"role": "user", "text": "   "}, "垃圾", {"role": "user", "text": "有效"}]
        messages = _history_messages(history)
        self.assertEqual([m["content"] for m in messages], ["ok", "有效"])

    def test_truncates_to_limit(self):
        history = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"消息{i}"} for i in range(30)]
        messages = _history_messages(history)
        self.assertLessEqual(len(messages), CHAT_HISTORY_LIMIT)
        self.assertEqual(messages[-1]["content"], "消息29", "保留最近的")


class ServerStoreTests(unittest.TestCase):
    """服务器管理：宽松校验 + 密码草稿覆盖 + 回显不带密码。"""

    def test_validate_normalizes_and_defaults(self):
        catalog, error = validate_servers([
            {"id": "s1", "label": "生产", "host": "192.168.1.100", "username": "root", "_password_draft": "pw1"},
        ], default="")
        self.assertEqual(error, "")
        self.assertEqual(catalog["default"], "s1")
        self.assertEqual(catalog["servers"][0]["password"], "pw1", "草稿密码应覆盖")
        self.assertEqual(catalog["servers"][0]["port"], 22, "缺省端口 22")

    def test_validate_lenient_keeps_values(self):
        catalog, error = validate_servers([
            {"id": "s1", "host": "", "port": "abc", "username": ""},
        ], default="")
        self.assertEqual(error, "")
        self.assertEqual(catalog["servers"][0]["port"], 22, "非法端口兜底 22")
        self.assertEqual(catalog["servers"][0]["username"], "root")

    def test_validate_empty_rejected(self):
        _, error = validate_servers([], default="")
        self.assertIn("至少", error)

    def test_duplicate_id_auto_deduped(self):
        catalog, error = validate_servers([
            {"id": "s1", "host": "a"}, {"id": "s1", "host": "b"},
        ], default="")
        self.assertEqual(error, "")
        ids = [entry["id"] for entry in catalog["servers"]]
        self.assertEqual(ids[1], "s1-2")

    def test_public_servers_hides_password(self):
        result = public_servers({"servers": [{"id": "s1", "host": "x", "password": "secret"}], "default": "s1"})
        self.assertNotIn("password", result[0])
        self.assertTrue(result[0]["has_password"])

    def test_write_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "servers.json"
            catalog, error = validate_servers([
                {"id": "s1", "label": "生产", "host": "1.2.3.4", "username": "root", "password": "pw"},
            ], default="")
            write_server_catalog(path, catalog)
            roundtrip = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(roundtrip["servers"][0]["host"], "1.2.3.4")

    def test_client_fails_gracefully_on_unreachable(self):
        from agentdesk_core.server_client import ServerClient

        client = ServerClient(host="192.0.2.1", username="root", password="x", port=22, timeout=2)
        result = client.test_connection()
        self.assertIn("ok", result)
        self.assertFalse(result["ok"], "不可达地址应优雅失败")


class HistoryMessageTests(unittest.TestCase):
    """模型历史裁剪：只留最近 N 条、忽略非法项。"""

    def test_keeps_recent_messages(self):
        history = [{"role": "user", "text": "一"}, {"role": "assistant", "text": "二"},
                   {"role": "user", "text": "三"}]
        messages = _history_messages(history)
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["role"], "user")

    def test_ignores_invalid_entries(self):
        history = [{"role": "user", "text": "ok"}, {"role": "system", "text": "忽略"},
                   {"role": "user"}, {"role": "user", "text": "   "}, "垃圾", {"role": "user", "text": "有效"}]
        messages = _history_messages(history)
        self.assertEqual([m["content"] for m in messages], ["ok", "有效"])

    def test_truncates_to_limit(self):
        history = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"消息{i}"} for i in range(30)]
        messages = _history_messages(history)
        self.assertLessEqual(len(messages), CHAT_HISTORY_LIMIT)
        self.assertEqual(messages[-1]["content"], "消息29", "保留最近的")


class ServerStoreTests(unittest.TestCase):
    """服务器管理：宽松校验 + 密码草稿覆盖 + 回显不带密码。"""

    def test_validate_normalizes_and_defaults(self):
        catalog, error = validate_servers([
            {"id": "s1", "label": "生产", "host": "192.168.1.100", "username": "root", "_password_draft": "pw1"},
        ], default="")
        self.assertEqual(error, "")
        self.assertEqual(catalog["default"], "s1")
        self.assertEqual(catalog["servers"][0]["password"], "pw1", "草稿密码应覆盖")
        self.assertEqual(catalog["servers"][0]["port"], 22, "缺省端口 22")

    def test_validate_lenient_keeps_values(self):
        catalog, error = validate_servers([
            {"id": "s1", "host": "", "port": "abc", "username": ""},
        ], default="")
        self.assertEqual(error, "")
        self.assertEqual(catalog["servers"][0]["port"], 22, "非法端口兜底 22")
        self.assertEqual(catalog["servers"][0]["username"], "root")

    def test_validate_empty_rejected(self):
        _, error = validate_servers([], default="")
        self.assertIn("至少", error)

    def test_duplicate_id_auto_deduped(self):
        catalog, error = validate_servers([
            {"id": "s1", "host": "a"}, {"id": "s1", "host": "b"},
        ], default="")
        self.assertEqual(error, "")
        ids = [entry["id"] for entry in catalog["servers"]]
        self.assertEqual(ids[1], "s1-2")

    def test_public_servers_hides_password(self):
        result = public_servers({"servers": [{"id": "s1", "host": "x", "password": "secret"}], "default": "s1"})
        self.assertNotIn("password", result[0])
        self.assertTrue(result[0]["has_password"])

    def test_write_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "servers.json"
            catalog, error = validate_servers([
                {"id": "s1", "label": "生产", "host": "1.2.3.4", "username": "root", "password": "pw"},
            ], default="")
            write_server_catalog(path, catalog)
            roundtrip = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(roundtrip["servers"][0]["host"], "1.2.3.4")

    def test_client_fails_gracefully_on_unreachable(self):
        from agentdesk_core.server_client import ServerClient

        client = ServerClient(host="192.0.2.1", username="root", password="x", port=22, timeout=2)
        result = client.test_connection()
        self.assertIn("ok", result)
        self.assertFalse(result["ok"], "不可达地址应优雅失败")


class ModelStoreTests(unittest.TestCase):
    """宽松校验：填啥存啥，只有结构性底线（非空、id 去重）。"""

    def test_validate_accepts_and_normalizes(self):
        catalog, error = validate_models([
            {"id": "m1", "label": "模型一", "model": "model-1", "base_url": "https://api.example.com/",
             "wire_api": "chat"},
        ], default="")
        self.assertEqual(error, "")
        self.assertEqual(catalog["default"], "m1")
        self.assertEqual(catalog["models"][0]["base_url"], "https://api.example.com", "结尾斜杠应去除")

    def test_validate_lenient_keeps_user_values(self):
        """格式不再拦：小写变量名、任意协议、空 label 都照存。"""
        catalog, error = validate_models([
            {"id": "m1", "label": "", "model": "m", "base_url": "not-a-url", "wire_api": "grpc"},
        ], default="")
        self.assertEqual(error, "")
        self.assertEqual(catalog["models"][0]["label"], "m1", "空 label 用 id 兜底")
        self.assertEqual(catalog["models"][0]["wire_api"], "grpc")

    def test_validate_empty_rejected(self):
        _, error = validate_models([], default="")
        self.assertIn("至少", error)

    def test_duplicate_id_auto_deduped(self):
        catalog, error = validate_models([
            {"id": "m1", "label": "A", "model": "m", "base_url": "https://x",},
            {"id": "m1", "label": "B", "model": "m", "base_url": "https://x",},
        ], default="")
        self.assertEqual(error, "")
        ids = [entry["id"] for entry in catalog["models"]]
        self.assertEqual(len(ids), len(set(ids)), "重复 id 应自动去重")
        self.assertEqual(ids[1], "m1-2")

    def test_write_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sub" / "models.json"
            catalog, error = validate_models([
                {"id": "m1", "label": "模型", "model": "m-1", "base_url": "https://x",},
            ], default="")
            self.assertEqual(error, "")
            write_model_catalog(path, catalog)
            self.assertEqual(read_model_catalog(path)["models"][0]["label"], "模型")

    def test_api_key_stored_directly(self):
        """密钥直存 models.json：api_key 照存，表单草稿覆盖旧值，展示字段不落盘。"""
        catalog, error = validate_models([
            {"id": "m1", "label": "A", "model": "m", "base_url": "https://x",
             "api_key": "sk-old", "key_masked": "sk-o••••ld", "_key_value": "sk-new"},
        ], default="")
        self.assertEqual(error, "", error)
        self.assertNotIn("key_masked", catalog["models"][0])
        self.assertNotIn("_key_value", catalog["models"][0])
        self.assertEqual(catalog["models"][0]["api_key"], "sk-new", "草稿应覆盖旧值")

    def test_api_key_without_draft_kept(self):
        catalog, error = validate_models([
            {"id": "m1", "label": "A", "model": "m", "base_url": "https://x", "api_key": "sk-keep"},
        ], default="")
        self.assertEqual(error, "", error)
        self.assertEqual(catalog["models"][0]["api_key"], "sk-keep", "未填草稿时保留原密钥")

    def test_mask_key(self):
        self.assertEqual(mask_key("sk-1234567890abcd"), "sk-1••••abcd")
        self.assertEqual(mask_key(""), "")
        self.assertEqual(mask_key("short"), "•••••")


class BridgeDispatchTests(unittest.TestCase):
    """chat 消息按模块分发：未锁定模型时走 agent_for_model。"""

    def _bridge(self, module):
        bridge = DesktopBridge.__new__(DesktopBridge)  # 跳过 __init__，手工装状态
        bridge.settings = MagicMock(model_id="m1", model_label="M1")
        bridge.settings.modules = (module,)
        bridge.settings.default_module_id = module.id
        bridge.agent = MagicMock()
        bridge.agents = {}
        bridge.lock = threading.Lock()
        bridge.request_context = threading.local()
        bridge.active_requests = {}
        bridge.pending = {}
        bridge.pending_events = {}
        bridge.pending_by_request = {}
        return bridge

    def _run(self, bridge, module):
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.run_chat("req-1", "问题", module.id, "m1", [], threading.Event(), "")

    def test_chat_module_uses_selected_model(self):
        module = MagicMock(id="chat", mode="chat", allow_files=False, model_locked=False)
        bridge = self._bridge(module)
        other_agent = MagicMock()
        with patch.object(bridge, "agent_for_model", return_value=other_agent) as agent_for_model:
            self._run(bridge, module)
        agent_for_model.assert_called_once_with("m1")
        other_agent.run_chat.assert_called_once()

    def test_save_keeps_old_password_when_draft_empty(self):
        """表单密码留空时，保存应保留磁盘上的旧密码，而不是清空。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "servers.json"
            write_server_catalog(path, {"default": "s1", "servers": [
                {"id": "s1", "label": "生产", "host": "1.2.3.4", "username": "root", "password": "旧密码"},
            ]})
            bridge = DesktopBridge.__new__(DesktopBridge)
            with patch("run_agentdesk_bridge.servers_config_path", return_value=path), \
                 patch.object(bridge, "reload"), \
                 patch.object(bridge, "_send_servers_config"), \
                 contextlib.redirect_stdout(io.StringIO()):
                bridge._save_servers({"servers": [
                    {"id": "s1", "label": "生产", "host": "1.2.3.4", "username": "root", "password": ""},
                ], "default": "s1"})
            saved = read_server_catalog(path)
            self.assertEqual(saved["servers"][0]["password"], "旧密码", "留空应保留旧密码")


    def test_save_keeps_old_password_when_draft_empty(self):
        """表单密码留空时，保存应保留磁盘上的旧密码，而不是清空。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "servers.json"
            write_server_catalog(path, {"default": "s1", "servers": [
                {"id": "s1", "label": "生产", "host": "1.2.3.4", "username": "root", "password": "旧密码"},
            ]})
            bridge = DesktopBridge.__new__(DesktopBridge)
            with patch("run_agentdesk_bridge.servers_config_path", return_value=path), \
                 patch.object(bridge, "reload"), \
                 patch.object(bridge, "_send_servers_config"), \
                 contextlib.redirect_stdout(io.StringIO()):
                bridge._save_servers({"servers": [
                    {"id": "s1", "label": "生产", "host": "1.2.3.4", "username": "root", "password": ""},
                ], "default": "s1"})
            saved = read_server_catalog(path)
            self.assertEqual(saved["servers"][0]["password"], "旧密码", "留空应保留旧密码")


    def test_locked_module_ignores_model_switch(self):
        module = MagicMock(id="chat", mode="chat", allow_files=False, model_locked=True)
        bridge = self._bridge(module)
        bridge.agent.run_chat.return_value = "ok"
        with patch.object(bridge, "agent_for_model") as agent_for_model:
            self._run(bridge, module)
        agent_for_model.assert_not_called()
        bridge.agent.run_chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
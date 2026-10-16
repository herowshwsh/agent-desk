"""离线端到端验证：模块分发、history 传递、trace 字段与审计隔离。

不发真实网络请求，也不连真实服务器。
运行：.venv-mcp/bin/python tests/e2e_modules.py
"""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# 用临时配置跑，绝不依赖本机真实的 models.json / servers.json
_TMP = ROOT / "tests" / "_tmp" / "e2e-config"
_TMP.mkdir(parents=True, exist_ok=True)
(_TMP / "models.json").write_text(json.dumps({
    "default": "e2e",
    "models": [{"id": "e2e", "label": "E2E", "model": "e2e",
                "base_url": "https://example.invalid", "api_key": "sk-test"}],
}, ensure_ascii=False), encoding="utf-8")
(_TMP / "servers.json").write_text(json.dumps({
    "default": "s1",
    "servers": [{"id": "s1", "label": "S1", "host": "127.0.0.1", "port": 22,
                 "username": "root", "password": "pw", "connect_timeout": 1}],
}, ensure_ascii=False), encoding="utf-8")
os.environ["AGENT_MODEL_CONFIG"] = str(_TMP / "models.json")
os.environ["AGENT_SERVERS_CONFIG"] = str(_TMP / "servers.json")
os.environ["AGENT_MODULES_CONFIG"] = str(ROOT / "modules.json")
os.environ["AGENT_MODEL_ID"] = "e2e"

import _stub_bridge_run as stub  # noqa: E402
import run_agentdesk_bridge as rb  # noqa: E402

RECORDER = stub.install_stubs()
CAPTURED = []
_real_emit = rb.emit


def _capture(payload):
    CAPTURED.append(payload)
    return _real_emit(payload)


rb.emit = _capture


def run_case(name, payload, expect, checker=None):
    CAPTURED.clear()
    RECORDER.clear()
    bridge = rb.DesktopBridge()
    bridge.handle(payload)
    deadline = time.time() + 8
    while time.time() < deadline:
        if any(item.get("type") == "chat_done" for item in CAPTURED):
            break
        time.sleep(0.1)
    bridge.close()

    done = [item for item in CAPTURED if item.get("type") == "chat_done"]
    answer = done[-1].get("answer", "") if done else ""
    module = done[-1].get("module") if done else None
    passed = expect in answer
    if checker:
        passed = passed and checker(RECORDER, CAPTURED)
    print(f"{'PASS' if passed else 'FAIL'} | {name:<22} | module={module} | answer={answer[:50]}")
    if not passed:
        print(f"       recorder={ {k: v for k, v in RECORDER.items() if k != 'messages'} }")
    return passed


def main() -> int:
    results = []

    results.append(
        run_case(
            "chat 纯转发",
            {"type": "chat", "id": "a", "question": "解释CAP", "module_id": "chat", "model_id": "", "history": []},
            "MODE=chat",
            lambda rec, _cap: rec.get("path") == "chat",
        )
    )

    results.append(
        run_case(
            "chat 带 history",
            {
                "type": "chat",
                "id": "b",
                "question": "再短点",
                "module_id": "chat",
                "model_id": "",
                "history": [{"role": "user", "text": "解释CAP"}, {"role": "assistant", "text": "一致性/可用性/分区容错性"}],
            },
            "MODE=chat",
            # messages = system + history(2) + 当前问题，所以 history 长度 = len - 2。
            lambda rec, _cap: rec.get("history_len") == 2 and "对话助手" in rec.get("chat_system", ""),
        )
    )

    results.append(
        run_case(
            "chat 提服务器不执行",
            {"type": "chat", "id": "c", "question": "重启我的服务器", "module_id": "chat", "model_id": "", "history": []},
            "MODE=chat",
            lambda rec, cap: rec.get("path") == "chat" and not any(item.get("type") == "approval_request" for item in cap),
        )
    )

    results.append(
        run_case(
            "agent 走意图路由",
            {"type": "chat", "id": "d", "question": "查询服务器信息", "module_id": "server", "model_id": "", "history": []},
            "MODE=agent",
            lambda rec, _cap: rec.get("path") == "agent_answer" and rec.get("call_count", 0) >= 2,
        )
    )

    results.append(
        run_case(
            "未知模块回落默认",
            {"type": "chat", "id": "e", "question": "查询服务器信息", "module_id": "不存在的模块", "model_id": "", "history": []},
            "MODE=agent",
            lambda rec, _cap: rec.get("path") == "agent_answer",
        )
    )

    results.append(
        run_case(
            "缺省模块走 agent",
            {"type": "chat", "id": "f", "question": "查询服务器信息", "model_id": "", "history": []},
            "MODE=agent",
        )
    )

    print("\n--- trace / audit 隔离 ---")
    trace_file = ROOT / "agentdesk_trace.jsonl"
    recent = []
    if trace_file.exists():
        for line in trace_file.read_text(encoding="utf-8").splitlines()[-12:]:
            if line.strip():
                try:
                    recent.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    chat_traces = [item for item in recent if item.get("mode") == "chat"]
    agent_traces = [item for item in recent if "action" in item]
    print(f"{'PASS' if chat_traces else 'FAIL'} | trace 记录了 chat 模式: {len(chat_traces)} 条")
    print(f"{'PASS' if agent_traces else 'FAIL'} | trace 记录了 agent action: {len(agent_traces)} 条")
    results.append(bool(chat_traces))
    results.append(bool(agent_traces))

    audit_file = ROOT / "agentdesk_audit.jsonl"
    audit_text = audit_file.read_text(encoding="utf-8") if audit_file.exists() else ""
    leak = '"mode": "chat"' in audit_text
    print(f"{'PASS' if not leak else 'FAIL'} | audit 未混入 chat 记录: {not leak}")
    results.append(not leak)

    passed = sum(1 for item in results if item)
    print(f"\n总计 {passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
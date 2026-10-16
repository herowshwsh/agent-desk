"""只保存多轮指代解析需要的少量会话记忆。"""

import json
from pathlib import Path
from typing import Any, Dict


ALLOWED_KEYS = {"last_action", "last_test_id", "last_test_name", "history"}


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Memory 文件损坏：{self.path}") from error
        if not isinstance(data, dict):
            raise ValueError("Memory 文件必须是 JSON 对象")
        return {key: value for key, value in data.items() if key in ALLOWED_KEYS}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        clean = {key: value for key, value in self.state.items() if key in ALLOWED_KEYS}
        self.path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        self.path.chmod(0o600)

    def update(self, action: str, plan: Dict[str, Any], answer: str, question: str) -> None:
        self.state["last_action"] = action
        if isinstance(plan.get("id"), int) and not isinstance(plan.get("id"), bool):
            self.state["last_test_id"] = plan["id"]
        if isinstance(plan.get("name"), str) and plan["name"].strip():
            self.state["last_test_name"] = plan["name"].strip()
        history = self.state.get("history", [])
        if not isinstance(history, list):
            history = []
        history.append({"question": question, "answer": answer})
        self.state["history"] = history[-6:]
        self.save()

"""JSONL Trace 和写操作审计。"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _safe(value: Any) -> Any:
    text = str(value)
    text = re.sub(r"(?i)(api[_-]?key|token|password)\s*[:=]\s*[^,\s}]+", r"\1=[REDACTED]", text)
    return text[:1000]


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_record = {key: _safe(value) if key in {"error", "message"} else value for key, value in record.items()}
    safe_record["timestamp"] = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(safe_record, ensure_ascii=False) + "\n")
    path.chmod(0o600)

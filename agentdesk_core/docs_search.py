"""轻量级项目文档和源码关键词搜索。"""

from pathlib import Path
from typing import Any, Dict, List


SEARCHABLE_SUFFIXES = {
    ".md", ".mdx", ".txt", ".ts", ".tsx", ".js", ".jsx", ".py", ".java",
    ".go", ".sql", ".json", ".yml", ".yaml", ".xml",
}
SKIP_DIRECTORIES = {".git", ".venv", ".venv-mcp", "node_modules", "dist", "build", "__pycache__"}


def search_backend_docs(root: Path, keyword: str, max_results: int = 8) -> Dict[str, Any]:
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("keyword 不能为空")
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"搜索目录不存在：{root}"}

    terms = [term.lower() for term in keyword.split() if term.strip()]
    results: List[Dict[str, Any]] = []
    for path in root.rglob("*"):
        if len(results) >= max_results:
            break
        if not path.is_file() or path.suffix.lower() not in SEARCHABLE_SUFFIXES:
            continue
        if any(directory in SKIP_DIRECTORIES for directory in path.parts):
            continue
        try:
            if path.stat().st_size > 512 * 1024:
                continue
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if all(term in line.lower() for term in terms):
                results.append({
                    "file": str(path.relative_to(root)),
                    "line": line_number,
                    "snippet": line.strip()[:500],
                })
                break
    return {"ok": True, "root": str(root), "keyword": keyword, "count": len(results), "results": results}

"""把本地文件内容组装进用户问题，供 chat 模块转发给模型。

安全边界：只读文本类文件；限制单文件大小与数量；目录只做浅层扫描；
读取失败不中断请求，跳过并附说明。
"""

import difflib
import os
import shutil
import time
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 20
MAX_TOTAL_CHARS = 400_000
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go",
    ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".sh",
    ".bash", ".zsh", ".sql", ".html", ".htm", ".css", ".scss", ".vue", ".xml",
    ".csv", ".tsv", ".log", ".env", ".gitignore", ".dockerfile", ".properties",
}
SKIP_DIRECTORIES = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vscode"}


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    return path.name in {".env", ".gitignore", "Dockerfile", "Makefile"}


def decode_text(data: bytes) -> str:
    """按 BOM → UTF-8 → GBK 的顺序探测解码；HR 系统导出的 GBK 文件也能读。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gbk")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def read_file_content(path: Path) -> tuple[str, str]:
    """读单个文件，返回 (内容或错误说明, 状态)。状态: ok / skipped。"""
    try:
        size = path.stat().st_size
    except OSError as error:
        return f"[无法读取 {path.name}：{error}]", "skipped"
    if not path.is_file():
        return f"[跳过 {path.name}：不是普通文件]", "skipped"
    if size > MAX_FILE_BYTES:
        return f"[跳过 {path.name}：超过 {MAX_FILE_BYTES // 1024}KB 大小限制]", "skipped"
    try:
        return decode_text(path.read_bytes()), "ok"
    except OSError as error:
        return f"[无法读取 {path.name}：{error}]", "skipped"


def _files_from_path(raw: str, notes: list[str]) -> list[Path]:
    """把用户选的路径展开成文件列表：文件直接收，目录做浅层扫描。"""
    path = Path(raw).expanduser()
    try:
        if path.is_file():
            return [path]
        if path.is_dir():
            picked = [p for p in sorted(path.iterdir()) if p.is_file() and _is_text_file(p)][:MAX_FILES]
            hidden_skipped = len([p for p in path.iterdir() if p.is_file() and not _is_text_file(p)])
            if hidden_skipped:
                notes.append(f"目录 {path.name} 下有 {hidden_skipped} 个非文本文件未读取")
            if not picked:
                notes.append(f"目录 {path.name} 下没有可读的文本文件")
            return picked
        notes.append(f"路径不存在：{raw}")
    except OSError as error:
        notes.append(f"无法访问 {raw}：{error}")
    return []


def collect_files(attachments: Any) -> tuple[list[Path], list[str]]:
    """把前端传来的路径数组展开成去重后的文件列表与提示信息。"""
    notes: list[str] = []
    if not isinstance(attachments, list):
        return [], notes
    seen: set[str] = set()
    files: list[Path] = []
    for raw in attachments:
        if not isinstance(raw, str) or not raw.strip():
            continue
        for path in _files_from_path(raw.strip(), notes):
            key = str(path.resolve())
            if key in seen:
                continue
            if len(files) >= MAX_FILES:
                notes.append(f"附件超过 {MAX_FILES} 个，{path.name} 未读取")
                return files, notes
            seen.add(key)
            files.append(path)
    return files, notes


def compose_question_with_files(question: str, attachments: Any) -> tuple[str, str]:
    """把附件内容拼进问题，返回 (增强后的问题, 给前端的提示)。"""
    files, notes = collect_files(attachments)
    if not files:
        return question, "；".join(notes)

    blocks: list[str] = []
    total_chars = 0
    skipped_by_budget = 0
    for path in files:
        content, status = read_file_content(path)
        if status != "ok" or total_chars + len(content) > MAX_TOTAL_CHARS:
            skipped_by_budget += 1
            notes.append(f"{path.name} 超出总内容预算，未读取")
            continue
        total_chars += len(content)
        blocks.append(f"【文件：{path.name}】\n{content}")
    if skipped_by_budget == len(files):
        return question, "；".join(notes) if notes else "附件内容为空"

    composed = (
        f"{question}\n\n"
        "---\n"
        "以下是用户选择的本地文件内容，回答时优先依据这些材料；"
        "材料里没有的信息要明确说明，不要编造。\n\n"
        + "\n\n".join(blocks)
    )
    return composed, "；".join(notes)


# ---------- 工作空间模式 ----------

MAX_TREE_ENTRIES = 500
MAX_TREE_DEPTH = 6


def _inside_workspace(workspace: Path, target: Path) -> bool:
    """判断 target 是否在 workspace 内（兼容 Python 3.9，不用 is_relative_to）。"""
    try:
        workspace_real = os.path.realpath(str(workspace))
        target_real = os.path.realpath(str(target))
        return os.path.commonpath([workspace_real, target_real]) == workspace_real
    except (ValueError, OSError):
        return False


def build_file_tree(workspace: Path) -> str:
    """生成工作空间的文件树（相对路径列表），供模型了解有哪些资源。"""
    lines: list[str] = []
    base = Path(os.path.realpath(str(workspace)))

    def walk(directory: Path, prefix: str, depth: int) -> None:
        if depth > MAX_TREE_DEPTH or len(lines) >= MAX_TREE_ENTRIES:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if len(lines) >= MAX_TREE_ENTRIES:
                lines.append(prefix + "…（超出展示上限，其余未列出）")
                return
            if entry.name.startswith(".") or entry.name in SKIP_DIRECTORIES:
                continue
            if entry.name.endswith(".bak"):
                continue  # 写入备份是恢复手段，不进文件树，避免模型误读误改
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                walk(entry, f"{prefix}{entry.name}/", depth + 1)
            else:
                lines.append(f"{prefix}{entry.name}")

    walk(base, "", 1)
    header = f"工作空间 {base.name} 的文件结构（共 {len(lines)} 项）："
    return header + "\n" + "\n".join(lines)


def read_workspace_file(workspace: Path, relative_path: Any) -> str:
    """读取工作空间内的单个文件，供模型的 read_file 工具回调。路径越界直接拒绝。"""
    if not isinstance(relative_path, str) or not relative_path.strip():
        return "错误：缺少 path 参数。"
    rel = relative_path.strip()
    if Path(rel).is_absolute():
        return f"错误：{rel} 不在工作空间内，只能读取工作空间里的文件。"
    target = Path(os.path.realpath(str(workspace))) / rel.lstrip("/")
    if not _inside_workspace(workspace, target):
        return f"错误：{rel} 不在工作空间内，只能读取工作空间里的文件。"
    if not target.exists():
        return f"错误：{rel} 不存在。"
    if not target.is_file():
        return f"错误：{rel} 是目录，只能读文件。"
    content, status = read_file_content(target)
    return content


def _backup_target(target: Path) -> str:
    """覆盖前备份原文件（时间戳后缀），返回给用户看的说明文字。"""
    stamp = time.strftime("%H%M%S")
    backup = target.with_name(f"{target.name}.{stamp}.bak")
    try:
        shutil.copy2(target, backup)
        return f" 原文件已备份为 {backup.name}。"
    except OSError:
        return " （原文件备份失败，仍继续写入。）"


def build_diff(old: str, new: str, max_lines: int = 60) -> str:
    """生成确认卡片用的简版 diff（unified 风格，超长截断）。"""
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "当前", "修改后", lineterm=""))
    body = lines[2:] if len(lines) > 2 else []
    if not body:
        return "（内容没有变化）"
    text = "\n".join(body[:max_lines])
    if len(body) > max_lines:
        text += f"\n…（其余 {len(body) - max_lines} 行省略）"
    return text


def write_workspace_file(workspace: Path, relative_path: Any, content: Any) -> tuple[str, str]:
    """在工作空间内写入文件，覆盖前自动备份原文件。

    路径越界直接拒绝；调用方（agent）必须先取得用户确认才调用本函数。
    返回 (给模型看的结果说明, 状态)。
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        return "错误：缺少 path 参数。", "rejected"
    rel = relative_path.strip()
    if Path(rel).is_absolute():
        return f"错误：{rel} 不在工作空间内，只能写工作空间里的文件。", "rejected"
    if not isinstance(content, str) or not content:
        return "错误：缺少 content 参数（要写入的完整内容）。", "rejected"
    workspace = Path(os.path.realpath(str(workspace)))
    target = workspace / rel.lstrip("/")
    if not _inside_workspace(workspace, target):
        return f"错误：{rel} 不在工作空间内，只能写工作空间里的文件。", "rejected"
    if target.is_dir():
        return f"错误：{rel} 是目录，不能当文件写入。", "rejected"

    backup_note = ""
    if target.exists():
        backup_note = _backup_target(target)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as error:
        return f"错误：写入 {rel} 失败：{error}", "failed"
    size = len(content.encode("utf-8"))
    return f"已写入 {rel}（{size} 字节）。{backup_note}", "ok"


def edit_workspace_file(workspace: Path, relative_path: Any, old_text: Any, new_text: Any) -> tuple[str, str]:
    """补丁式编辑：把文件里唯一出现的 old_text 替换为 new_text（覆盖前自动备份）。

    old_text 必须在文件中恰好出现一次，否则拒绝 —— 防止模型误替换。
    调用方（agent）必须先取得用户确认才调用本函数。
    返回 (给模型看的结果说明, 状态)。
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        return "错误：缺少 path 参数。", "rejected"
    rel = relative_path.strip()
    if Path(rel).is_absolute():
        return f"错误：{rel} 不在工作空间内，只能编辑工作空间里的文件。", "rejected"
    if not isinstance(old_text, str) or not old_text:
        return "错误：缺少 old_text 参数（要被替换的原文片段）。", "rejected"
    if not isinstance(new_text, str):
        return "错误：缺少 new_text 参数（替换后的内容，删除内容时传空字符串）。", "rejected"
    if len(old_text) > MAX_FILE_BYTES or len(new_text) > MAX_FILE_BYTES:
        return f"错误：编辑片段超过 {MAX_FILE_BYTES // 1024}KB 上限，请拆分处理。", "rejected"

    workspace = Path(os.path.realpath(str(workspace)))
    target = workspace / rel.lstrip("/")
    if not _inside_workspace(workspace, target):
        return f"错误：{rel} 不在工作空间内，只能编辑工作空间里的文件。", "rejected"
    if not target.is_file():
        return f"错误：{rel} 不存在或不是文件；新建文件请用 write_file。", "rejected"

    current, status = read_file_content(target)
    if status != "ok":
        return f"错误：无法读取 {rel}，放弃编辑。", "failed"
    occurrences = current.count(old_text)
    if occurrences == 0:
        return "错误：文件里没有找到要替换的片段。请先用 read_file 核对内容（注意空格与缩进），再重试。", "rejected"
    if occurrences > 1:
        return f"错误：要替换的片段在文件里出现了 {occurrences} 次。请扩大片段范围，保证唯一。", "rejected"

    backup_note = _backup_target(target)
    try:
        target.write_text(current.replace(old_text, new_text, 1), encoding="utf-8")
    except OSError as error:
        return f"错误：编辑 {rel} 失败：{error}", "failed"
    added = len(new_text.splitlines()) if new_text else 0
    removed = len(old_text.splitlines())
    return f"已编辑 {rel}（替换 1 处，+{added}/-{removed} 行）。{backup_note}", "ok"
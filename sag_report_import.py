"""将用户选定的报告片段作为 Markdown 交给 SAG Wiki 文件监视器。"""
import hashlib
import os
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import tempfile

from sag_evidence import INDEX_SCHEMA_VERSION, REPORT_EXCERPTS_DIR


MAX_IMPORT_CHARS = 24000
LONG_PREFIX = "\\\\?\\"
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)")


def sections(text):
    blocks, current, fence = [], [], ""
    has_fence = False
    for line in text.splitlines(keepends=True):
        marker = FENCE.match(line)
        if marker:
            has_fence = True
            symbol, tail = marker.groups()
            if not fence:
                fence = symbol
            elif symbol[0] == fence[0] and len(symbol) >= len(fence) and not tail.strip():
                fence = ""
        if not fence and HEADING.match(line) and "".join(current).strip():
            blocks.append("".join(current).strip())
            current = []
        current.append(line)
    if "".join(current).strip():
        blocks.append("".join(current).strip())
    if len(blocks) == 1 and not has_fence:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", blocks[0]) if part.strip()]
        if len(paragraphs) > 1:
            blocks = paragraphs
    result = []
    for number, body in enumerate(blocks, 1):
        match = HEADING.match(body)
        title = match[1].strip().rstrip("#").strip() if match else f"片段 {number}"
        result.append({"id": number, "title": title[:160], "markdown": body})
    return result


def destination(index, scope):
    """校验目标确实位于 SAG Wiki 已管理的范围内。"""
    target = scope / REPORT_EXCERPTS_DIR
    if not scope.is_dir() or target.resolve() != target:
        raise ValueError("SAG 资料目录不可用。")
    try:
        with closing(sqlite3.connect(index.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            if db.execute("PRAGMA user_version").fetchone() != (INDEX_SCHEMA_VERSION,):
                raise ValueError("SAG Wiki 索引版本暂不支持。")
            roots = [Path(path.removeprefix(LONG_PREFIX)).resolve()
                     for path, in db.execute("SELECT path FROM roots WHERE status IN ('ready','partial','indexing')")]
            exclusions = [Path(path.removeprefix(LONG_PREFIX)).resolve()
                          for path, in db.execute("SELECT path FROM root_exclusions")]
    except sqlite3.Error:
        raise ValueError("无法读取 SAG Wiki 索引，请检查 SAG_DATA_DIR。") from None
    if not any(scope.is_relative_to(root) for root in roots):
        raise ValueError("请先在 SAG Wiki 中为指定资料目录建立索引。")
    if any(target.is_relative_to(path) for path in exclusions):
        raise ValueError("SAG Wiki 已排除此目录，请改用已索引的目录。")
    return target


def save(target, run_id, report_type, version, selected):
    body = "\n\n".join(section["markdown"] for section in selected)
    if len(body) > MAX_IMPORT_CHARS:
        raise ValueError(f"每次最多保存 {MAX_IMPORT_CHARS} 字符，请减少所选片段。")
    label = " ".join(report_type.split())
    content = (
        f"# 报告摘录 · {label}\n\n"
        f"> 来自 OSINT 研究报告，任务 {run_id}，报告版本 {version}。"
        "这是模型生成报告的摘录，请沿原引用复核。\n\n"
        f"{body}\n"
    ).encode("utf-8")
    fingerprint = hashlib.sha256(content).hexdigest()
    target.mkdir(exist_ok=True)
    path = target / f"report-{fingerprint[:32]}.md"
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise OSError("同名文件内容不一致")
        return path, fingerprint
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path, fingerprint


def is_indexed(index, path, fingerprint):
    try:
        with closing(sqlite3.connect(index.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            return db.execute(
                """SELECT 1 FROM documents d JOIN chunks c ON c.document_id=d.id
                WHERE d.path IN (?, ?) AND d.hash=? LIMIT 1""",
                (str(path), LONG_PREFIX + str(path), fingerprint),
            ).fetchone() is not None
    except sqlite3.Error:
        return False

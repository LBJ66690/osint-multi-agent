"""用本机 SAG Wiki 对证据缺口做有界检索。"""
import asyncio
from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import sqlite3
from time import monotonic

from evidence_quality import assess, conflicts, similar
from schemas import EvidenceItem

INDEX_SCHEMA_VERSION = 9
REPORT_EXCERPTS_DIR = "osint-report-excerpts"


def targets(evidence, questions):
    coverage = assess(evidence, [question.question for question in questions])["coverage"]
    disputed = {row["subquestion"] for row in conflicts(evidence)}
    return [question for question in questions
            if question.question in disputed or coverage[question.question]["status"] != "sufficient"]


def settings(config):
    scope = os.getenv("OSINT_SAG_SCOPE", "").strip()
    if not scope:
        return None
    data_dir = os.getenv("SAG_DATA_DIR") or config.ROOT / "outputs/sag-wiki-local/desktop-data"
    return (Path(data_dir) / "sag.sqlite").resolve(), Path(scope).resolve()


async def search(index, scope, question, run_id, port):
    return await asyncio.to_thread(_search, index, scope, question, run_id, port)


def _search(index, scope, question, run_id, port):
    phrases = [q.strip().casefold()[:160] for q in question.search_queries[:3] if q.strip()]
    tokens = {word for phrase in phrases for word in re.split(r"\s+", phrase) if len(word) >= 3}
    if not phrases:
        return []
    deadline = monotonic() + 6
    candidates = []
    prefix = str(scope).rstrip(os.sep) + os.sep
    long_prefix = "\\\\?\\"
    with closing(sqlite3.connect(index.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.set_progress_handler(lambda: int(monotonic() > deadline), 5000)
        if db.execute("PRAGMA user_version").fetchone() != (INDEX_SCHEMA_VERSION,):
            raise ValueError("SAG Wiki 索引版本不兼容")
        rows = db.execute(
            """SELECT d.path, d.hash, c.start_line, c.end_line FROM documents d
            JOIN chunks c ON c.document_id=d.id
            WHERE (substr(d.path,1,?)=? COLLATE NOCASE OR substr(d.path,1,?)=? COLLATE NOCASE)
              AND NOT EXISTS (SELECT 1 FROM notes n WHERE n.document_id=d.id)
            ORDER BY d.path, c.start_line LIMIT 4000""",
            (len(prefix), prefix, len(long_prefix + prefix), long_prefix + prefix),
        )
        previous, lines = None, []
        for name, indexed_hash, start, end in rows:
            if monotonic() > deadline:
                raise TimeoutError("SAG 本地证据扫描超时")
            if name != previous:
                previous, lines = name, []
                try:
                    original = Path(name.removeprefix(long_prefix)).resolve()
                    path = original.relative_to(scope)
                    if REPORT_EXCERPTS_DIR not in path.parts and original.suffix.lower() in {".md", ".txt"}:
                        with original.open("rb") as handle:
                            data = handle.read(2_000_001)
                        if len(data) <= 2_000_000 and hashlib.sha256(data).hexdigest() == indexed_hash:
                            lines = data.decode("utf-8-sig").splitlines()
                except (OSError, ValueError):
                    pass
            if not 1 <= start <= end <= len(lines):
                continue
            body = "\n".join(lines[start - 1:end]).strip()[:2400]
            text = body.casefold()
            score = 3 * sum(phrase in text for phrase in phrases) + sum(token in text for token in tokens)
            if score:
                candidates.append((score, path, start, end, body))
    items = []
    for _, path, start, end, body in sorted(candidates, key=lambda row: -row[0])[:3]:
        digest = hashlib.sha256(f"{path.as_posix()}:{start}:{end}".encode()).hexdigest()[:24]
        items.append(EvidenceItem(
            subquestion=question.question,
            title=f"本地资料 · {path.as_posix()} 第 {start}–{end} 行",
            content=body, provider="sag",
            source=f"http://127.0.0.1:{port}/api/runs/{run_id}/sag-evidence/{digest}",
        ))
    return items


def merge(evidence, candidates):
    combined = list(evidence)
    for item in candidates:
        if not any(row.subquestion == item.subquestion and
                   (row.source == item.source or similar(row.model_dump(), item.model_dump()))
                   for row in combined):
            combined.append(item)
    return combined

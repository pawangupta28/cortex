"""Structure-aware chunking: split on document structure (headings), not fixed windows.

Each chunk keeps its heading path ("Returns > Damaged items") so retrieval and
citations know where it came from, and long sections are split on paragraph
boundaries so a chunk never cuts a sentence in half.
"""
from __future__ import annotations

import hashlib
import re

from cortex.core.models import Chunk

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _cid(doc_id: str, path: str, i: int, seq: int) -> str:
    # seq makes ids unique when a heading repeats inside one document (common in filings)
    return hashlib.sha1(f"{doc_id}|{path}|{i}|{seq}".encode()).hexdigest()[:16]


def chunk_markdown(doc_id: str, text: str, max_words: int = 120, metadata: dict | None = None) -> list[Chunk]:
    metadata = metadata or {}
    sections: list[tuple[str, list[str]]] = []
    path: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if any(line.strip() for line in buf):
            sections.append((" > ".join(path), list(buf)))
        buf.clear()

    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            path[:] = path[: level - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    flush()

    chunks: list[Chunk] = []
    for section_path, lines in sections:
        paragraphs = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
        cur: list[str] = []
        words = 0
        part = 0
        for p in paragraphs:
            n = len(p.split())
            if cur and words + n > max_words:
                chunks.append(Chunk(id=_cid(doc_id, section_path, part, len(chunks)), doc_id=doc_id, text="\n\n".join(cur),
                                    section_path=section_path, metadata=metadata))
                part += 1
                cur, words = [], 0
            cur.append(p)
            words += n
        if cur:
            chunks.append(Chunk(id=_cid(doc_id, section_path, part, len(chunks)), doc_id=doc_id, text="\n\n".join(cur),
                                section_path=section_path, metadata=metadata))
    return chunks

import re
from dataclasses import dataclass, field
from app.extractors import DocumentBlock

MAX_CHUNK_CHARS = 1500
@dataclass
class SemanticUnit:
    title: str; section: str; subsection: str; content: str; pages: list[int] = field(default_factory=list); table_json: dict | None = None; metadata: dict = field(default_factory=dict); kind: str = "concept"

def _split_content(content: str) -> list[str]:
    if len(content) <= MAX_CHUNK_CHARS: return [content]
    sentences = re.split(r"(?<=[.!?])\s+", content)
    result=[]; current=""
    for sentence in sentences:
        if current and len(current)+len(sentence)+1 > MAX_CHUNK_CHARS:
            result.append(current.strip()); current=""
        current = f"{current} {sentence}".strip()
    if current: result.append(current)
    return result

def build_semantic_units(blocks: list[DocumentBlock]) -> list[SemanticUnit]:
    """Create one unit per heading section and split only inside that section."""
    units=[]; headings=[]; paragraphs=[]; pages=set(); metadata={}; kind="concept"
    def flush():
        nonlocal paragraphs,pages,metadata,kind
        if not paragraphs: return
        content="\n\n".join(paragraphs).strip(); section=headings[0] if headings else "Document"; subsection=headings[-1] if len(headings)>1 else ""
        for part in _split_content(content):
            units.append(SemanticUnit(subsection or section, section, subsection, part, sorted(pages), None, {**metadata, "heading": subsection or section}, kind))
        paragraphs=[]; pages=set(); metadata={}; kind="concept"
    for block in blocks:
        if block.kind == "heading":
            flush(); level=max(1, block.heading_level); headings[:]=headings[:level-1]; headings.append(block.title or block.text); continue
        if block.kind == "table":
            flush(); current=headings or ["Document"]; units.append(SemanticUnit(current[-1], current[0], current[-1] if len(current)>1 else "", block.text, [block.page_number] if block.page_number else [], block.table_json, dict(block.metadata), "table")); continue
        text=block.text.strip()
        if not text: continue
        if block.page_number: pages.add(block.page_number)
        metadata.update(block.metadata); paragraphs.append(text)
        if block.kind == "ocr": kind="ocr"
    flush(); return units

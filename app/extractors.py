from __future__ import annotations

import csv
import io
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


@dataclass
class DocumentBlock:
    kind: str
    text: str
    page_number: int | None = None
    title: str = ""
    heading_level: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    table_json: dict[str, Any] | None = None


@dataclass
class ExtractionResult:
    blocks: list[DocumentBlock]
    structured_markdown: str
    layout_metadata: dict[str, Any] = field(default_factory=dict)
    parser: str = "local"
    warnings: list[str] = field(default_factory=list)


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".csv",
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".bmp",
    ".webp",
}


# Kept as a compatibility alias. Strict ingestion accepts only these extensions and sends all of them to LlamaParse.
LLAMA_PARSE_EXTENSIONS = SUPPORTED_EXTENSIONS


def fetch_url(url: str, max_bytes: int) -> tuple[str, bytes]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only http and https URLs are supported")
    _reject_private_host(parsed.hostname)
    response = httpx.get(
        url,
        follow_redirects=True,
        timeout=30,
        headers={"User-Agent": "KnowledgeIntelligenceEngine/0.1"},
    )
    if response.url.host:
        _reject_private_host(response.url.host)
    response.raise_for_status()
    payload = response.content
    if len(payload) > max_bytes:
        raise ValueError("Remote content exceeds the configured upload size limit")
    return response.headers.get("content-type", "text/html"), payload


def _reject_private_host(hostname: str) -> None:
    if hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("Private and local URLs are not allowed")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
        if any(ipaddress.ip_address(address).is_private for address in addresses):
            raise ValueError("Private and local URLs are not allowed")
    except socket.gaierror as exc:
        raise ValueError("URL hostname could not be resolved") from exc


def parse_file(path: Path, mime_type: str | None = None) -> list[DocumentBlock]:
    return parse_file_result(path, mime_type).blocks


def parse_file_result(path: Path, mime_type: str | None = None) -> ExtractionResult:
    """Parse every accepted input through LlamaParse, with no local fallback."""
    suffix = path.suffix.lower()
    from app.config import get_settings
    from app.llama_parse import parse_with_llama_result

    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {suffix or mime_type or 'unknown'}")
    settings = get_settings()
    if not settings.llama_parse_enabled:
        raise RuntimeError("LlamaParse-only ingestion is enabled, but LLAMA_PARSE_ENABLED is false")
    # parse_with_llama_result raises a clear configuration error if the API key is missing.
    # Deliberately do not catch LlamaParseError or call any local parser here.
    return parse_with_llama_result(path, mime_type, settings)


def _blocks_to_markdown(blocks: list[DocumentBlock]) -> str:
    lines: list[str] = []
    for block in blocks:
        if block.kind == "heading":
            lines.append(f"{'#' * max(1, block.heading_level)} {block.title or block.text}")
        elif block.kind == "table" and block.table_json:
            rows = block.table_json.get("rows", [])
            if rows:
                lines.append("| " + " | ".join(rows[0]) + " |")
                lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
                lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
        elif block.text.strip():
            lines.append(block.text.strip())
        if lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


def _layout_metadata(blocks: list[DocumentBlock]) -> dict[str, Any]:
    return {
        "block_count": len(blocks),
        "pages": sorted({block.page_number for block in blocks if block.page_number}),
        "blocks": [
            {
                "kind": block.kind,
                "page_number": block.page_number,
                "heading_level": block.heading_level,
                "metadata": block.metadata,
            }
            for block in blocks
        ],
    }


def _parse_text(path: Path) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            blocks.append(
                DocumentBlock("heading", match.group(2).strip(), heading_level=len(match.group(1)))
            )
        else:
            blocks.append(DocumentBlock("paragraph", line))
    return blocks


def _parse_html(html: str) -> list[DocumentBlock]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for node in soup.find_all(["script", "style", "noscript", "template", "nav", "header", "footer", "aside", "form"]):
        node.decompose()
    for node in soup.find_all(class_=re.compile(r"cookie|advert|sidebar|navigation|menu|popup", re.I)):
        node.decompose()
    blocks: list[DocumentBlock] = []
    seen: set[tuple[str, str]] = set()
    for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"]):
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text:
            continue
        key = (node.name, text)
        if key in seen:
            continue
        seen.add(key)
        if node.name.startswith("h"): 
            blocks.append(DocumentBlock("heading", text, heading_level=int(node.name[1:])))
        elif node.name == "table":
            rows = [
                [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
                for row in node.find_all("tr")
            ]
            blocks.append(_table_block(rows, source="html"))
        else:
            blocks.append(DocumentBlock("paragraph", text))
    return blocks


def _parse_pdf(path: Path) -> list[DocumentBlock]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: list[DocumentBlock] = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            for paragraph in re.split(r"\n\s*\n", text):
                clean = " ".join(paragraph.split())
                if clean:
                    blocks.append(DocumentBlock("paragraph", clean, page_number=index))
        else:
            ocr = _ocr_pdf_page(path, index)
            if ocr:
                blocks.append(DocumentBlock("ocr", ocr, page_number=index, metadata={"ocr": True}))
    return blocks


def _parse_docx(path: Path) -> list[DocumentBlock]:
    from docx import Document

    document = Document(str(path))
    blocks: list[DocumentBlock] = []
    for paragraph in document.paragraphs:
        text = " ".join(paragraph.text.split())
        if not text:
            continue
        style = paragraph.style.name.lower() if paragraph.style else ""
        match = re.search(r"heading\s*(\d+)", style)
        blocks.append(
            DocumentBlock(
                "heading" if match else "paragraph",
                text,
                heading_level=int(match.group(1)) if match else 0,
            )
        )
    for table_index, table in enumerate(document.tables, start=1):
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        blocks.append(_table_block(rows, source=f"docx-table-{table_index}"))
    return blocks


def _parse_xlsx(path: Path) -> list[DocumentBlock]:
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), data_only=True, read_only=True)
    blocks: list[DocumentBlock] = []
    for sheet in workbook.worksheets:
        rows = [
            ["" if value is None else str(value).strip() for value in row]
            for row in sheet.iter_rows(values_only=True)
        ]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        blocks.append(
            DocumentBlock("heading", sheet.title, heading_level=1, metadata={"sheet": sheet.title})
        )
        blocks.append(
            _table_block(rows, source=f"sheet:{sheet.title}", metadata={"sheet": sheet.title})
        )
    return blocks


def _parse_csv(path: Path) -> list[DocumentBlock]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle))
    return [_table_block(rows, source="csv")] if rows else []


def _parse_pptx(path: Path) -> list[DocumentBlock]:
    from pptx import Presentation

    presentation = Presentation(str(path))
    blocks: list[DocumentBlock] = []
    for slide_number, slide in enumerate(presentation.slides, start=1):
        texts = [
            shape.text.strip()
            for shape in slide.shapes
            if hasattr(shape, "text") and shape.text.strip()
        ]
        if texts:
            blocks.append(
                DocumentBlock(
                    "slide",
                    "\n".join(texts),
                    page_number=slide_number,
                    metadata={"slide": slide_number},
                )
            )
    return blocks


def _parse_image(path: Path) -> list[DocumentBlock]:
    from PIL import Image

    with Image.open(path) as image:
        text = _ocr_image(image)
    return [DocumentBlock("ocr", text, page_number=1, metadata={"ocr": True})] if text else []


def _ocr_image_bytes(page: Any) -> str:
    try:
        from PIL import Image
        from pytesseract import image_to_string

        image = page.to_image(resolution=180).original
        return image_to_string(Image.open(io.BytesIO(image)))
    except Exception:
        return ""


def _ocr_pdf_page(path: Path, page_number: int) -> str:
    try:
        import fitz
        from PIL import Image
        from pytesseract import image_to_string

        document = fitz.open(str(path))
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
        return image_to_string(image)
    except Exception:
        return ""


def _ocr_image(image: Any) -> str:
    try:
        from pytesseract import image_to_string

        return image_to_string(image)
    except Exception:
        return ""


def _table_block(
    rows: list[list[str]],
    source: str,
    metadata: dict[str, Any] | None = None,
    page_number: int | None = None,
) -> DocumentBlock:
    normalized = [row for row in rows if any(cell.strip() for cell in row)]
    headers = normalized[0] if normalized else []
    records = [dict(zip(headers, row, strict=False)) for row in normalized[1:]] if headers else []
    natural = _table_to_text(headers, records)
    payload = {
        "source": source,
        "headers": headers,
        "rows": normalized,
        "records": records,
        "natural_language": natural,
    }
    return DocumentBlock(
        "table", natural, page_number=page_number, metadata=metadata or {}, table_json=payload
    )


def _table_to_text(headers: list[str], records: list[dict[str, str]]) -> str:
    if not headers:
        return ""
    if not records:
        return "Table columns: " + ", ".join(headers) + "."
    lines = ["Table data with columns " + ", ".join(headers) + ":"]
    for record in records:
        lines.append("; ".join(f"{key}: {value}" for key, value in record.items() if value))
    return "\n".join(lines)

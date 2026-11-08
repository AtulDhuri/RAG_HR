from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.extractors import DocumentBlock, ExtractionResult, _table_block


class LlamaParseError(RuntimeError):
    """A controlled error from the LlamaParse V2 API."""


LLAMA_PARSE_ENDPOINT = "https://api.cloud.llamaindex.ai/api/v2/parse/upload"
LLAMA_PARSE_RESULT_ENDPOINT = "https://api.cloud.llamaindex.ai/api/v2/parse/{job_id}"


def parse_with_llama(path: Path, mime_type: str | None, settings: Settings) -> list[DocumentBlock]:
    return parse_with_llama_result(path, mime_type, settings).blocks


def parse_with_llama_result(
    path: Path, mime_type: str | None, settings: Settings
) -> ExtractionResult:
    api_key = (
        settings.llama_cloud_api_key.get_secret_value() if settings.llama_cloud_api_key else ""
    )
    if not api_key:
        raise LlamaParseError("LLAMA_CLOUD_API_KEY is not configured")

    configuration = {
        "tier": settings.llama_parse_tier,
        "version": settings.llama_parse_version,
        "processing_options": {
            "ocr_parameters": {"languages": settings.llama_parse_ocr_languages}
        },
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    content_type = mime_type or "application/octet-stream"
    try:
        with path.open("rb") as handle:
            response = httpx.post(
                LLAMA_PARSE_ENDPOINT,
                headers=headers,
                files={"file": (path.name, handle, content_type)},
                data={"configuration": json.dumps(configuration)},
                timeout=max(
                    settings.llama_parse_request_timeout_seconds,
                    settings.llama_parse_job_timeout_seconds,
                ),
            )
        _raise_for_api_error(response, "create parse job")
        payload = response.json()
        if not isinstance(payload, dict):
            raise LlamaParseError("LlamaParse returned an invalid response during create parse job")
        job_id = _job_id(payload)
        if not job_id:
            raise LlamaParseError("LlamaParse did not return a parse job id")
        result = _poll_result(job_id, headers, settings)
    except httpx.RequestError as exc:
        raise LlamaParseError(f"LlamaParse network error: {exc.__class__.__name__}") from exc
    blocks, structured_markdown, layout_metadata, warnings = _blocks_from_result(result)
    return ExtractionResult(
        blocks=blocks,
        structured_markdown=structured_markdown,
        layout_metadata=layout_metadata,
        parser="llamaparse",
        warnings=warnings,
    )


def _poll_result(job_id: str, headers: dict[str, str], settings: Settings) -> dict[str, Any]:
    deadline = time.monotonic() + min(
        settings.llama_parse_max_wait_seconds, settings.llama_parse_job_timeout_seconds
    )
    url = LLAMA_PARSE_RESULT_ENDPOINT.format(job_id=job_id)
    params = {"expand": "markdown,items,metadata"}
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                url,
                headers=headers,
                params=params,
                timeout=settings.llama_parse_request_timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise LlamaParseError(f"LlamaParse polling error: {exc.__class__.__name__}") from exc
        _raise_for_api_error(response, "retrieve parse result")
        payload = response.json()
        job = payload.get("job", payload)
        state = str(job.get("status", "")).upper()
        if state == "COMPLETED":
            return payload
        if state in {"FAILED", "CANCELLED"}:
            message = str(job.get("error_message") or f"parse job ended with {state}")
            raise LlamaParseError(message[:500])
        time.sleep(settings.llama_parse_poll_interval_seconds)
    raise LlamaParseError("LlamaParse job exceeded the configured polling timeout")


def _blocks_from_result(
    result: dict[str, Any],
) -> tuple[list[DocumentBlock], str, dict[str, Any], list[str]]:
    markdown_pages = _page_map(result.get("markdown"))
    item_pages = _page_map(result.get("items"))
    metadata_pages = _page_map(result.get("metadata"))
    blocks: list[DocumentBlock] = []
    warnings: list[str] = []
    for page_number in sorted(set(markdown_pages) | set(item_pages) | set(metadata_pages)):
        markdown_page = markdown_pages.get(page_number, {})
        item_page = item_pages.get(page_number, {})
        page_metadata = metadata_pages.get(page_number, {})
        base_metadata = {
            "parser": "llamaparse",
            "confidence": page_metadata.get("confidence"),
            "printed_page_number": page_metadata.get("printed_page_number"),
            "page_metadata": page_metadata,
        }
        items = item_page.get("items") or []
        if items:
            blocks.extend(_blocks_from_items(items, page_number, base_metadata))
        else:
            markdown = str(markdown_page.get("markdown") or "").strip()
            if markdown:
                blocks.extend(_blocks_from_markdown(markdown, page_number, base_metadata))
            elif page_number in metadata_pages:
                warnings.append(f"Page {page_number} had metadata but no markdown or items")
    structured_markdown = (
        str(result.get("markdown_full") or "").strip() or _blocks_to_markdown(blocks)
    )
    layout_metadata = {
        "page_count": len(set(markdown_pages) | set(item_pages) | set(metadata_pages)),
        "pages": [
            {"page_number": page, **metadata_pages.get(page, {})}
            for page in sorted(set(markdown_pages) | set(item_pages) | set(metadata_pages))
        ],
        "document": (result.get("metadata") or {}).get("document", {}),
        "warnings": warnings,
    }
    return blocks, structured_markdown, layout_metadata, warnings


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
        lines.append("")
    return "\n".join(lines).strip()


def _blocks_from_items(
    items: list[dict[str, Any]], page_number: int, metadata: dict[str, Any]
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    for item in items:
        item_type = str(item.get("type", "text")).lower()
        if item_type == "heading":
            title = str(item.get("value") or item.get("md") or "").strip()
            title = re.sub(r"^#+\s*", "", title)
            if title:
                blocks.append(
                    DocumentBlock(
                        "heading",
                        title,
                        page_number=page_number,
                        title=title,
                        heading_level=_heading_level(item.get("level")),
                        metadata={**metadata, "bbox": item.get("bbox"), "item_id": item.get("id")},
                    )
                )
        elif item_type == "table":
            rows = _table_rows(item.get("rows"))
            if rows:
                blocks.append(
                    _table_block(
                        rows,
                        source=f"llamaparse:page:{page_number}",
                        metadata={**metadata, "parse_concerns": item.get("parse_concerns") or []},
                        page_number=page_number,
                    )
                )
        else:
            text = str(item.get("value") or item.get("md") or "").strip()
            if text:
                blocks.append(
                    DocumentBlock(
                        "paragraph",
                        _strip_markdown(text),
                        page_number,
                        metadata={
                            **metadata,
                            "bbox": item.get("bbox"),
                            "item_id": item.get("id"),
                        },
                    )
                )
    return blocks


def _blocks_from_markdown(
    text: str, page_number: int, metadata: dict[str, Any]
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    lines = text.splitlines()
    paragraph: list[str] = []
    table_rows: list[list[str]] = []

    def flush_paragraph() -> None:
        if paragraph:
            value = " ".join(line.strip() for line in paragraph).strip()
            if value:
                blocks.append(
                    DocumentBlock(
                        "paragraph", _strip_markdown(value), page_number, metadata=metadata
                    )
                )
            paragraph.clear()

    def flush_table() -> None:
        if table_rows:
            blocks.append(
                _table_block(
                    table_rows[:], source=f"llamaparse:page:{page_number}", metadata=metadata
                )
            )
            table_rows.clear()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_table()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            flush_table()
            title = _strip_markdown(heading.group(2))
            blocks.append(
                DocumentBlock(
                    "heading",
                    title,
                    page_number,
                    title=title,
                    heading_level=len(heading.group(1)),
                    metadata=metadata,
                )
            )
            continue
        if line.startswith("|") and line.endswith("|"):
            flush_paragraph()
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                table_rows.append(cells)
            continue
        flush_table()
        paragraph.append(line)
    flush_paragraph()
    flush_table()
    return blocks


def _page_map(value: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    pages = value.get("pages")
    if not isinstance(pages, list):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        try:
            page_number = int(page.get("page_number"))
        except (TypeError, ValueError):
            continue
        if page.get("success", True) is not False:
            result[page_number] = page
    return result


def _table_rows(rows: Any) -> list[list[str]]:
    if not isinstance(rows, list):
        return []
    return [
        ["" if value is None else str(value) for value in row]
        for row in rows
        if isinstance(row, list)
    ]


def _heading_level(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _strip_markdown(value: str) -> str:
    value = re.sub(r"!\[[^]]*\]\([^)]*\)", "", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[*_`]+", "", value)
    return " ".join(value.split())


def _job_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("id")
    if value:
        return str(value)
    job = payload.get("job")
    return str(job["id"]) if isinstance(job, dict) and job.get("id") else None


def _raise_for_api_error(response: httpx.Response, operation: str) -> None:
    if response.is_success:
        return
    if response.status_code in {401, 403}:
        raise LlamaParseError(f"LlamaParse authorization failed during {operation}")
    if response.status_code == 429:
        raise LlamaParseError(f"LlamaParse rate limit reached during {operation}")
    if response.status_code == 499:
        raise LlamaParseError(
            f"LlamaParse closed the upload request during {operation}; "
            "increase LLAMA_PARSE_JOB_TIMEOUT_SECONDS or retry the upload"
        )
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            value = payload.get("detail") or payload.get("message") or payload
            detail = json.dumps(value, separators=(",", ":"))[:800]
        else:
            detail = str(payload)[:800]
    except (ValueError, json.JSONDecodeError):
        detail = response.text[:800]
    suffix = f": {detail}" if detail else ""
    raise LlamaParseError(
        f"LlamaParse returned HTTP {response.status_code} during {operation}{suffix}"
    )

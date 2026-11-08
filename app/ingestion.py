import hashlib
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.classification import classify_document, classification_dict
from app.config import get_settings
from app.domain_extraction import extract_domain_fields
from app.enrichment import enrich_chunk, extract_graph_facts
from app.extractors import parse_file_result
from app.models import (
    ChunkEmbedding,
    Document,
    DocumentVersion,
    Entity,
    IngestionLog,
    KnowledgeGraphFact,
    Section,
    SemanticChunk,
)
from app.providers import embedding_provider
from app.structure import build_semantic_units


def ingest_document(document_id: uuid.UUID, path: Path) -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        document = db.get(Document, document_id)
        if not document:
            return
        document.status = "processing"
        db.add(IngestionLog(document_id=document_id, stage="started", status="running", message="Ingestion started"))
        db.commit()

        extraction = parse_file_result(path, document.mime_type)
        _validate_extraction_quality(extraction)
        blocks = extraction.blocks
        quality_warnings = _extraction_quality_warnings(extraction)
        if quality_warnings:
            _log(db, document_id, "extraction_quality", "completed", "; ".join(quality_warnings))
        _log(db, document_id, "parsing", "completed", f"Parsed {len(blocks)} document blocks")
        table_count = sum(1 for block in blocks if block.kind == "table")
        _log(db, document_id, "tables", "completed", f"Extracted {table_count} tables")
        _log(db, document_id, "extraction", "completed", f"Extracted {len(blocks)} document blocks")
        units = build_semantic_units(blocks)
        if not units:
            raise ValueError("No readable content was extracted from the document")
        _log(db, document_id, "headings", "completed", "Identified document sections and headings")
        _log(
            db, document_id, "chunks", "completed", f"Built {len(units)} semantic chunks"
        )
        _log(
            db, document_id, "semantic_structure", "completed", f"Built {len(units)} semantic units"
        )

        classification = classify_document(extraction.structured_markdown)
        domain_fields = extract_domain_fields(extraction.structured_markdown, classification)
        _log(db, document_id, "classification", "completed", f"Classified as {classification.document_type}/{classification.domain}")

        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        version_count = db.scalar(
            select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == document_id)
        ) or 0
        version = DocumentVersion(
            document_id=document_id,
            version_number=version_count + 1,
            checksum=checksum,
            page_count=max((page for unit in units for page in unit.pages), default=0),
            metadata_json={
                "block_count": len(blocks),
                "unit_count": len(units),
                "parser": extraction.parser,
                "warnings": extraction.warnings,
                "classification": classification_dict(classification),
                "domain_fields": domain_fields,
            },
            structured_markdown=extraction.structured_markdown,
            layout_metadata=extraction.layout_metadata,
            document_type=classification.document_type,
            domain=classification.domain,
            classifier_confidence=classification.confidence,
        )
        db.add(version)
        db.flush()

        provider = embedding_provider()
        _log(db, document_id, "embeddings", "running", "Generating document embeddings")
        enriched: list[tuple[SemanticChunk, list[dict]]] = []
        for ordinal, unit in enumerate(units):
            data = enrich_chunk(unit.title, unit.section, unit.content, unit.pages, unit.table_json)
            section = Section(
                document_version_id=version.id,
                title=unit.title or unit.section,
                kind=unit.kind,
                level=2 if unit.subsection else 1,
                ordinal=ordinal,
                path=" / ".join(filter(None, [unit.section, unit.subsection])),
                content=unit.content,
            )
            db.add(section)
            db.flush()
            chunk = SemanticChunk(
                document_version_id=version.id,
                section_id=section.id,
                ordinal=ordinal,
                title=unit.title or "Untitled concept",
                section=unit.section,
                subsection=unit.subsection,
                topic=unit.title or unit.section,
                content=unit.content,
                metadata_json={
                    "parser": extraction.parser,
                    "layout": unit.metadata,
                    "document_type": classification.document_type,
                    "domain": classification.domain,
                },
                **data,
            )
            db.add(chunk)
            db.flush()
            enriched.append((chunk, data["entities"]))

        embedding_inputs = [chunk.embedding_text for chunk, _ in enriched]
        if any(not text.strip() or len(text) > 24000 for text in embedding_inputs):
            raise ValueError("Chunk embedding input is empty or exceeds the provider limit")
        vectors = provider.embed(embedding_inputs)
        if len(vectors) != len(enriched) or any(not vector or len(vector) != get_settings().embedding_dimension for vector in vectors):
            raise ValueError("Embedding verification failed: missing vector or incorrect dimension")
        for (chunk, entities), vector in zip(enriched, vectors, strict=True):
            _log(db, document_id, "embedding", "completed", f"chunk_id={chunk.id} status=embedded")
            db.add(ChunkEmbedding(chunk_id=chunk.id, embedding=vector, model=provider.model_name))
            for entity_data in entities:
                _get_or_create_entity(db, document.tenant_id, entity_data["name"], entity_data["type"])
            for fact in extract_graph_facts(chunk.content, entities):
                db.add(KnowledgeGraphFact(
                    tenant_id=document.tenant_id,
                    document_version_id=version.id,
                    evidence_chunk_id=chunk.id,
                    subject=str(fact["subject"]),
                    predicate=str(fact["predicate"]),
                    object=str(fact["object"]),
                    confidence=float(fact["confidence"]),
                ))
        db.flush()
        _log(db, document_id, "embeddings", "completed", f"Generated {len(vectors)} embeddings")
        _log(db, document_id, "storage", "completed", "Knowledge base storage completed")
        document.status = "completed"
        document.error_message = None
        db.add(IngestionLog(document_id=document_id, stage="indexing", status="completed", message="Knowledge index published"))
        db.commit()
    except Exception as exc:
        db.rollback()
        document = db.get(Document, document_id)
        if document:
            document.status = "failed"
            document.error_message = str(exc)[:2000]
            db.add(IngestionLog(document_id=document_id, stage="failed", status="failed", message=str(exc)[:2000]))
            db.commit()
    finally:
        db.close()


def _get_or_create_entity(db: Session, tenant_id: str, name: str, entity_type: str) -> Entity:
    entity = db.scalar(select(Entity).where(Entity.tenant_id == tenant_id, Entity.canonical_name == name))
    if entity:
        return entity
    entity = Entity(tenant_id=tenant_id, canonical_name=name, entity_type=entity_type, aliases=[])
    db.add(entity)
    db.flush()
    return entity


def _log(db: Session, document_id: uuid.UUID, stage: str, status: str, message: str) -> None:
    db.add(IngestionLog(document_id=document_id, stage=stage, status=status, message=message))
    db.commit()


def _validate_extraction_quality(extraction) -> None:
    """Fail ingestion when LlamaParse produced no usable source evidence."""
    blocks = extraction.blocks or []
    readable = [block for block in blocks if (block.text or "").strip()]
    if not readable:
        raise ValueError("Document cannot be ingested: LlamaParse returned no readable text or tables")
    total_text = sum(len((block.text or "").strip()) for block in readable)
    if total_text < 20:
        raise ValueError("Document cannot be ingested: extracted content is too short to index")
    layout = extraction.layout_metadata or {}
    page_count = layout.get("page_count") or len(layout.get("pages", []))
    if page_count and not any(block.page_number for block in readable):
        raise ValueError("Document cannot be ingested: page metadata exists but extracted blocks have no page provenance")


def _extraction_quality_warnings(extraction) -> list[str]:
    warnings = list(extraction.warnings or [])
    layout = extraction.layout_metadata or {}
    pages = layout.get("pages", [])
    blocks_by_page = {block.page_number for block in extraction.blocks if block.page_number and (block.text or "").strip()}
    for page in pages:
        page_number = page.get("page_number") if isinstance(page, dict) else page
        if page_number and page_number not in blocks_by_page:
            warnings.append(f"Page {page_number} has no readable extracted block")
    return list(dict.fromkeys(warnings))

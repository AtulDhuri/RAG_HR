from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.answering import answer_query
from app.auth import AdminPrincipal, authenticate_admin, current_tenant, require_admin
from app.config import get_settings
from app.db import Base, engine, get_db
from app.extractors import SUPPORTED_EXTENSIONS, fetch_url
from app.ingestion import ingest_document
from app.evaluation import run_evaluation
from app.retrieval import retrieve
from app.validation import validate_knowledge_base
from app.models import Document, DocumentVersion, IngestionLog, SemanticChunk, ChunkEmbedding
from app.schemas import (
    AnswerResponse,
    DocumentAccepted,
    DocumentStatus,
    LoginRequest,
    LoginResponse,
    QueryRequest,
    UrlSourceRequest,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.post("/api/v1/auth/login", response_model=LoginResponse)
def login(request: LoginRequest) -> LoginResponse:
    result = authenticate_admin(request.username, request.password)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )
    token, expires_in = result
    return LoginResponse(access_token=token, expires_in=expires_in)


@app.post(
    "/api/v1/documents", response_model=DocumentAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _admin: AdminPrincipal = Depends(require_admin),
    tenant_id: str = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> DocumentAccepted:
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported file type: {suffix or 'unknown'}")
    payload = await file.read()
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(413, "File exceeds the configured upload size limit")
    document_id = uuid.uuid4()
    storage_key = Path(tenant_id) / str(document_id) / filename
    path = settings.storage_dir / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    document = Document(
        id=document_id,
        tenant_id=tenant_id,
        name=filename,
        source_type="file",
        mime_type=file.content_type,
        storage_key=str(storage_key),
        status="queued",
    )
    db.add(document)
    db.commit()
    background_tasks.add_task(ingest_document, document_id, path)
    return DocumentAccepted(
        document_id=document_id,
        status="queued",
        name=filename,
        message="Document accepted for knowledge extraction",
    )


@app.get("/api/v1/documents")
def list_documents(_admin: AdminPrincipal = Depends(require_admin), tenant_id: str = Depends(current_tenant), db: Session = Depends(get_db)) -> list[dict]:
    documents = db.scalars(select(Document).where(Document.tenant_id == tenant_id).order_by(Document.created_at.desc())).all()
    return [{"document_id": str(document.id), "name": document.name, "status": document.status, "created_at": document.created_at.isoformat() if document.created_at else None, "error_message": document.error_message} for document in documents]


@app.delete("/api/v1/documents/{document_id}")
def delete_document(document_id: uuid.UUID, _admin: AdminPrincipal = Depends(require_admin), tenant_id: str = Depends(current_tenant), db: Session = Depends(get_db)) -> dict:
    document = db.scalar(select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id))
    if not document:
        raise HTTPException(404, "Document not found")
    storage_path = (settings.storage_dir / document.storage_key).resolve()
    storage_root = settings.storage_dir.resolve()
    if storage_root not in storage_path.parents:
        raise HTTPException(400, "Document storage path is invalid")
    # Database foreign keys cascade from documents to versions, chunks, embeddings,
    # logs, graph facts, and citations. The tenant predicate prevents cross-tenant deletion.
    db.execute(text("DELETE FROM documents WHERE id = :document_id AND tenant_id = :tenant_id"), {"document_id": document_id, "tenant_id": tenant_id})
    db.commit()
    if storage_path.is_file():
        storage_path.unlink()
    parent = storage_path.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return {"document_id": str(document_id), "deleted": True, "message": "Document, related database data, and uploaded file deleted"}


@app.get("/api/v1/documents/{document_id}", response_model=DocumentStatus)
def document_status(
    document_id: uuid.UUID,
    _admin: AdminPrincipal = Depends(require_admin),
    tenant_id: str = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> DocumentStatus:
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
    )
    if not document:
        raise HTTPException(404, "Document not found")
    version = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
    )
    logs = db.scalars(
        select(IngestionLog)
        .where(IngestionLog.document_id == document_id)
        .order_by(IngestionLog.created_at.asc())
    ).all()
    stages = _ingestion_stages(document.status, logs)
    progress = round(sum(stage["progress"] for stage in stages) / len(stages)) if stages else 0
    return DocumentStatus(
        document_id=document.id,
        name=document.name,
        status=document.status,
        error_message=document.error_message,
        version_id=version.id if version else None,
        page_count=version.page_count if version else None,
        document_type=version.document_type if version else None,
        domain=version.domain if version else None,
        classifier_confidence=version.classifier_confidence if version else None,
        progress=progress,
        stages=stages,
    )


def _ingestion_stages(document_status: str, logs: list[IngestionLog]) -> list:
    definitions = [
        ("uploading", "Uploading document"),
        ("parsing", "Parsing document structure"),
        ("tables", "Extracting tables"),
        ("headings", "Identifying sections & headings"),
        ("chunks", "Creating chunks"),
        ("embeddings", "Generating embeddings"),
        ("storage", "Storing in knowledge base"),
    ]
    by_stage = {log.stage: log for log in logs}
    result = []
    for key, label in definitions:
        log = by_stage.get(key)
        if log and log.status == "completed":
            stage_status, stage_progress = "completed", 100
        elif log and log.status == "running":
            stage_status, stage_progress = "running", 50
        elif log and log.status == "failed":
            stage_status, stage_progress = "failed", 0
        elif key == "uploading" and logs:
            stage_status, stage_progress = "completed", 100
        elif document_status == "completed":
            stage_status, stage_progress = "completed", 100
        else:
            stage_status, stage_progress = "pending", 0
        result.append(
            {
                "key": key,
                "label": label,
                "status": stage_status,
                "progress": stage_progress,
                "message": log.message if log else "",
            }
        )
    return result


@app.post("/api/v1/query", response_model=AnswerResponse)
def query_documents(
    request: QueryRequest,
    tenant_id: str = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> AnswerResponse:
    return answer_query(db, tenant_id, request.query, request.document_ids, request.top_k)


@app.post("/api/v1/admin/validate")
def validate_knowledge_base_route(_admin: AdminPrincipal = Depends(require_admin), tenant_id: str = Depends(current_tenant), db: Session = Depends(get_db)) -> dict:
    return validate_knowledge_base(db, tenant_id)


@app.post("/api/v1/admin/evaluation")
def evaluation(_admin: AdminPrincipal = Depends(require_admin), tenant_id: str = Depends(current_tenant), db: Session = Depends(get_db)) -> dict:
    return run_evaluation(db, tenant_id)


@app.post("/api/v1/admin/retrieval")
def test_retrieval(request: QueryRequest, _admin: AdminPrincipal = Depends(require_admin), tenant_id: str = Depends(current_tenant), db: Session = Depends(get_db)) -> dict:
    from app.query_understanding import analyze_query
    analysis = analyze_query(request.query)
    _, diagnostics = retrieve(db, tenant_id, analysis.expanded_queries, request.document_ids, limit=10)
    return {"query": request.query, "query_analysis": analysis.as_dict(), "diagnostics": diagnostics}


@app.get("/api/v1/admin/chunks/{chunk_id}")
def view_chunk(chunk_id: uuid.UUID, _admin: AdminPrincipal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    chunk = db.get(SemanticChunk, chunk_id)
    if not chunk:
        raise HTTPException(404, "Chunk not found")
    embedding = db.get(ChunkEmbedding, chunk_id)
    return {"chunk_id": str(chunk.id), "title": chunk.title, "section": chunk.section, "subsection": chunk.subsection, "pages": chunk.source_pages, "content": chunk.content, "embedding": {"model": embedding.model, "dimension": len(embedding.embedding), "vector": list(embedding.embedding)} if embedding else None}


@app.post(
    "/api/v1/sources/url",
    response_model=DocumentAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_url(
    request: UrlSourceRequest,
    background_tasks: BackgroundTasks,
    _admin: AdminPrincipal = Depends(require_admin),
    tenant_id: str = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> DocumentAccepted:
    try:
        content_type, payload = await asyncio.to_thread(
            fetch_url, request.url, settings.max_upload_bytes
        )
    except Exception as exc:
        raise HTTPException(400, f"Unable to fetch URL: {exc}") from exc
    document_id = uuid.uuid4()
    storage_key = Path(tenant_id) / str(document_id) / "source.html"
    path = settings.storage_dir / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    document = Document(
        id=document_id,
        tenant_id=tenant_id,
        name=request.url,
        source_type="url",
        mime_type=content_type,
        storage_key=str(storage_key),
        status="queued",
    )
    db.add(document)
    db.commit()
    background_tasks.add_task(ingest_document, document_id, path)
    return DocumentAccepted(
        document_id=document_id,
        status="queued",
        name=request.url,
        message="Website accepted for knowledge extraction",
    )


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    @app.get("/user", include_in_schema=False)
    def user_workspace() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/admin", include_in_schema=False)
    def admin_workspace() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

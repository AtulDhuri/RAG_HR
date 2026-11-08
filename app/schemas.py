import uuid
from typing import Any, Literal
from pydantic import BaseModel, Field

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
class LoginResponse(BaseModel):
    access_token: str; token_type: str = "bearer"; role: str = "admin"; expires_in: int
class DocumentAccepted(BaseModel):
    document_id: uuid.UUID; status: str; name: str; message: str
class IngestionStage(BaseModel):
    key: str; label: str; status: Literal["pending", "running", "completed", "failed"]; progress: int = Field(ge=0, le=100); message: str = ""
class DocumentStatus(BaseModel):
    document_id: uuid.UUID; name: str; status: str; error_message: str | None = None; version_id: uuid.UUID | None = None; page_count: int | None = None; document_type: str | None = None; domain: str | None = None; classifier_confidence: float | None = None; progress: int = Field(default=0, ge=0, le=100); stages: list[IngestionStage] = Field(default_factory=list)
class QueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=10_000); document_ids: list[uuid.UUID] | None = None; top_k: int = Field(default=5, ge=1, le=20)
class UrlSourceRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2_000)
class SourceCitation(BaseModel):
    document_name: str; section: str; page_numbers: list[int] = Field(default_factory=list); chunk_id: uuid.UUID; relevance_score: float; evidence_strength: float
class AnswerResponse(BaseModel):
    answer: str; confidence: Literal["High", "Medium", "Low"]; sources: list[SourceCitation]; reasoning_summary: str; related_topics: list[str] = Field(default_factory=list); query_analysis: dict[str, Any] = Field(default_factory=dict); diagnostics: dict[str, Any] = Field(default_factory=dict)

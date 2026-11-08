# RAG pipeline audit and change log

## Root cause found

The previous query path averaged the first eight expanded query embeddings, used a binary `ILIKE` check as its lexical signal, had no reranker, and rejected answers when an exact-token confidence score was low. Maternity terminology was also missing from the synonym map. The result could retrieve a relevant chunk but still return the generic insufficient-information response.

## Implemented

- Replaced vector-only retrieval with bounded hybrid retrieval:
  - vector search top 20 using the original question embedding
  - Okapi BM25 keyword search top 20 over the tenant corpus
  - result merge and chunk-ID deduplication
  - deterministic cross-encoder-compatible reranking using dense, BM25, phrase, heading, overlap, and graph evidence signals
  - final ranked evidence retained for diagnostics and up to 10 chunks sent to the answer provider
- Added maternity, pregnancy, maternal, childbirth, parental, and family-leave terminology expansion.
- Changed chunking to heading-based units. Each heading section stays independent and sections over 1,500 characters split only within that section.
- Preserved heading, subsection, page, and chunk metadata in the existing schema and chunk metadata JSON.
- Hardened HTML extraction by removing scripts, styles, navigation, headers, footers, sidebars, forms, cookie/advertisement regions, HTML attributes, and duplicate nested text before chunking.
- Added embedding validation for empty inputs, provider output count, vector dimensions, and provider-size limits. Each embedded chunk records an ingestion log entry containing its chunk ID and status.
- Changed answer gating to evidence-based behavior. Explicitly supported evidence always produces an answer and high confidence. Confidence and coverage no longer reject an explicitly supported answer.
- Updated answer instructions to return `Information not found in uploaded documents.` only when evidence does not support the answer.
- Added per-query diagnostics containing the query, embedding preview/dimension, retrieval strategy, top vector and keyword results, scores, merged results, reranked results, chunks sent to the LLM, final prompt, confidence, coverage, and decision reason.
- Added a visible diagnostics panel to the web UI. Source cards continue to display document name, section, and page numbers without exposing raw chunk IDs.
- Added admin endpoints:
  - `POST /api/v1/admin/retrieval` for retrieval inspection
  - `GET /api/v1/admin/chunks/{chunk_id}` for chunk and embedding inspection
  - `POST /api/v1/admin/evaluation` for the requested six-question evaluation suite
- Added `app/evaluation.py` with retrieval, chunk relevance, reranker, answer, and citation accuracy metrics.

## Compatibility

No database migration or existing table change was introduced. Existing upload, URL ingestion, document status, answer, citation, and authentication flows remain in place. Existing stored chunks must be re-ingested to receive the improved heading metadata and enriched terminology.

## Verification completed

- `python -m compileall app` passes.
- FastAPI imports successfully and exposes the new admin routes.
- A direct HTML fixture verified that navigation/style content is removed and Maternity and Attendance become separate chunks.

## Operational note

The implementation uses a deterministic application reranker because the project does not currently depend on a hosted or local cross-encoder model. The reranker stage is isolated in `app/retrieval.py`; a model-backed cross encoder can replace `_rerank` without changing the API or database schema.

## LlamaParse-only enforcement

The ingestion selector now routes every supported file and URL source directly through LlamaParse. Local PDF, DOCX, spreadsheet, text, HTML, image, and OCR parsers are no longer reachable from normal ingestion. Missing credentials, disabled LlamaParse, API failures, and parsing failures mark ingestion as failed with the visible error instead of silently using a local parser. `LLAMA_PARSE_FALLBACK_LOCAL` remains accepted for environment compatibility but is intentionally ignored.

## Knowledge-base validation agent

Added `app/validation.py` and `POST /api/v1/admin/validate`. The validator audits completed/failed documents, LlamaParse warnings and page metadata, empty content, chunk size/duplicates/incomplete boundaries/heading grouping, embedding completeness, 25 content-derived retrieval questions, grounded extractive answers, hallucination risk, citations, business-readiness questions, ten knowledge categories, scoring, production blockers, and recommendations. It never adds facts outside retrieved chunks. Scores below 85% return `Knowledge Base Requires Attention`.

## Local database operation

The validator does not require Docker. It connects through the existing `DATABASE_URL` setting using SQLAlchemy. PostgreSQL with pgvector must be running locally or on the configured database host.

## Document deletion

Added an authenticated document list and per-document deletion workflow. The admin UI now displays every uploaded document with a Delete button. The delete endpoint executes a tenant-scoped SQL `DELETE FROM documents` statement, allowing configured foreign-key cascades to remove versions, sections, chunks, embeddings, ingestion logs, graph facts, and citations, then removes the uploaded file and empty document directory from storage. It refuses invalid storage paths and prevents cross-tenant deletion.

## PostgreSQL prepared-statement fix

Disabled psycopg server-side prepared statements in the SQLAlchemy engine with `prepare_threshold=None`. This prevents `DuplicatePreparedStatement` errors when connections are reused through PgBouncer or another transaction-pooling layer.

## Answer refusal safeguard

Improved answer gating to ignore generic stopwords during explicit-evidence detection and added an extractive fallback. If the answer provider returns a generic refusal despite retrieved supporting evidence, the system now returns the best matching evidence sentences instead of displaying `Information not found in uploaded documents.` with high confidence.

## Four-layer retrieval upgrade

Added stronger query expansion with phrase n-grams, weighted expanded-query embeddings, BM25 over all expanded terms, parent-child section expansion, and optional sentence-transformers CrossEncoder reranking using `cross-encoder/ms-marco-MiniLM-L-6-v2`. Diagnostics now report whether the model-backed cross encoder or the deterministic fallback was used. Added `sentence-transformers` to project dependencies.

## OpenAI query understanding

Query analysis now uses the configured OpenAI model when available to classify intent and generate natural retrieval paraphrases. The prompt explicitly forbids answering or adding facts. Retrieval and answer generation remain grounded in indexed document chunks and embeddings; OpenAI query understanding cannot become a source of truth. A deterministic fallback remains available if the API is unavailable.

## Intelligent retrieval orchestration policy

Query understanding now uses OpenAI for intent, entities, topics, synonyms, and natural search rewrites when configured. The prompt forbids answering or adding facts. Answers remain grounded exclusively in retrieved indexed chunks and embeddings. Every response now exposes intent, expanded search terms, vector/BM25/reranker diagnostics, evidence-found state, final confidence, failure reasons, source document, section, chunk ID, and confidence. Unsupported answers use the explicit insufficient-evidence response.

## Confidence crash fix

Fixed a `NameError` in `_confidence` introduced when expanded query terms were added to evidence validation. The function now receives expanded queries explicitly, preventing query requests from failing with `analysis is not defined`.

## Extraction quality gate

Added a post-LlamaParse ingestion gate. Documents fail visibly when parsing returns no readable blocks, fewer than 20 extracted characters, or page metadata without page-linked extracted content. Page-level missing-content warnings are recorded without rejecting otherwise usable documents. The existing document status UI exposes the failure reason.

## Supabase Cloud deployment

Updated deployment documentation and service templates for clients using Supabase Cloud PostgreSQL with pgvector. The client machine now runs only the Knowledge Base API and local uploaded-file storage; Supabase stores database rows, chunks, and embeddings. The Linux service no longer waits for a local PostgreSQL service.

## Separate user and admin UI

Removed the visible Admin ingestion tab from the user interface. `/user` now exposes only the answer-only search experience. `/admin` directly opens the authenticated admin workspace for ingestion, validation, diagnostics, and deletion.

## Retrieval speed optimization

Reduced repeated query-understanding calls through an in-memory cache, reduced expansion embedding work to the first five expanded queries, optimized BM25 to tokenize the tenant corpus once per query, limited parent-child expansion anchors, limited CrossEncoder reranking to the highest merged candidates, and reduced the default answer context from up to 10 chunks to up to 8. OpenAI query-understanding timeout is capped at 10 seconds with a deterministic fallback.

## Retrieval latency optimization

Replaced per-query full-tenant corpus download and Python BM25 tokenization with PostgreSQL full-text ranking backed by a GIN expression index, while preserving the hybrid keyword retrieval leg and diagnostics. Added an HNSW cosine index for pgvector search and reduced the CrossEncoder rerank pool to 15 candidates. Added migration `0004_retrieval_indexes`.

## Expanded-query evidence matching fix

Fixed procedure-question rejection caused by combining all expanded queries into one large token set. Evidence support is now evaluated independently for each expanded query and the strongest matching variation wins. This prevents valid questions such as grievance-process questions from being rejected because the combined synonym vocabulary diluted the overlap score.

## Retrieval regression fallback

Added a compatibility keyword fallback when Supabase full-text search returns no rows, covering projects where migration 0004 has not yet been applied and phrases that PostgreSQL full-text normalization misses. This preserves the indexed fast path while preventing valid existing chunks from disappearing during the transition.
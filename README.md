# Knowledge Intelligence Engine

A document intelligence platform that converts heterogeneous files into structured knowledge, semantic concepts, entity relationships, and grounded answers with citations. It is deliberately more than a vector-search chatbot: ingestion preserves document hierarchy, Excel and tables retain business meaning, retrieval combines semantic, lexical, metadata, entity, and graph signals, and answer generation is constrained by retrieved evidence.

## Architecture

```text
FastAPI API -> Supabase Cloud PostgreSQL + pgvector
     |                     |
     +-> uploaded originals (local filesystem on client machine)
     +-> ingestion pipeline: LlamaParse -> structure -> semantic chunks -> enrich -> graph -> embeddings
     +-> query pipeline: OpenAI query understanding -> hybrid retrieve -> rerank -> grounded answer
```

The code keeps embeddings, reranking, and answer generation behind provider boundaries. Client deployments use Supabase Cloud for PostgreSQL/pgvector, LlamaParse for ingestion, and OpenAI for query understanding and model services.

The parser uses LlamaParse V2 exclusively for every supported upload and URL source: PDF, DOCX, XLSX, PPTX, CSV, TXT, Markdown, HTML, and image inputs. It uses the V2 multipart upload-and-parse endpoint, requests Agentic parsing with OCR, structured items, Markdown, page metadata, and table preservation, then polls the job until completion. Local parsers are not used. If LlamaParse is disabled, unavailable, or fails, ingestion fails visibly instead of silently switching parsers.

The Llama Cloud key is loaded from `.env` as `LLAMA_CLOUD_API_KEY`; never commit that file or print the key in logs. The V2 parser follows the documented `agentic` tier and `latest` version settings. Configure `LLAMA_PARSE_TIER`, `LLAMA_PARSE_VERSION`, OCR languages, and polling limits through environment variables.

## Run locally

1. Install Python 3.12.
2. Create a client-owned Supabase project, enable the `vector` extension in the Supabase SQL Editor, and copy the secure database connection string.
3. Copy `.env.example` to `.env` and set `DATABASE_URL` to the Supabase Cloud connection. Use `sslmode=require`.
4. Install the package and initialize the schema:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e ".[dev]"
   python -m app.db_init
   uvicorn app.main:app --reload
   ```

5. Open `http://localhost:8000/user` for user search or `http://localhost:8000/admin` for the authenticated admin workspace.

The development admin credentials are configurable with `ADMIN_USERNAME` and `ADMIN_PASSWORD`. Change them before any shared or production deployment. The development session token is held in memory by the API process; production deployments must replace it with OIDC/JWT authentication.

The API uses the server-configured `DEFAULT_TENANT_ID` for the browser workspace. Replace this development identity model with an OIDC/JWT gateway and tenant authorization before exposing the service publicly.

## Supported input

PDF, DOCX, XLSX, PPTX, CSV, TXT, Markdown, HTML, image files, and HTTP(S) website URLs. Scanned PDFs and images use LlamaParse OCR. LlamaParse preserves page, slide, sheet, table, heading, and section metadata.

## Production requirements

- Run schema changes through Alembic rather than `AUTO_CREATE_SCHEMA`.
- Put originals and derived artifacts in encrypted object storage.
- Configure a durable queue to run ingestion outside the API process.
- Replace development tenant headers with authenticated identity and authorization checks.
- Configure a real embedding provider and grounded answer provider.
- Add managed OCR for scanned documents, quotas, retention workflows, audit logging, metrics, tracing, backups, and secret-manager integration.

### Implemented LlamaParse phase

For LlamaParse-enabled inputs, ingestion now persists the canonical structured Markdown, page-level metadata, parser confidence, printed page numbers, item bounding boxes, table rows, and page-linked table citations on `document_versions`. The next stages classify the document and domain, extract conservative domain fields, preserve provenance on semantic chunks, generate embeddings, and index the result in PostgreSQL/pgvector. The document status response exposes the detected document type, domain, and classifier confidence.

Apply the provenance migration before processing new documents:

```powershell
alembic upgrade head
```

The LlamaParse V2 request uses the documented multipart upload endpoint and nested OCR configuration. See the [LlamaParse V2 migration guide](https://developers.llamaindex.ai/python/cloud/llamaparse/migration-v1-to-v2) and [response format reference](https://developers.llamaindex.ai/llamaparse/parse/guides/response-format/) for the upstream contract.

## Run the API and admin workspace

From the project root in PowerShell:

```powershell
# Apply Supabase migrations and start the API# Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# Apply database migrations
alembic upgrade head

# Run app/main.py through Uvicorn
python -m uvicorn app.main:app --reload
```

Open `http://localhost:8000` for the admin workspace, or open `http://localhost:8000/docs` for the API documentation. In development, sign in with the values configured by `ADMIN_USERNAME` and `ADMIN_PASSWORD`, which default to `admin` and `admin`.

To test the admin login from PowerShell:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/auth/login `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"admin"}'
```

### Search text index note

`semantic_chunks.search_text` is intentionally not backed by a plain B-tree index because generated search text can exceed PostgreSQL's 2,704-byte B-tree entry limit. Apply the migration after updating the code:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

### OpenAI embedding 400 errors

Use the application’s setting names in `.env`:

```dotenv
OPENAI_API_KEY=your-new-key
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=384
```

Restart Uvicorn after changing `.env`. The embedding provider limits oversized generated chunk input and now reports the API error detail instead of only `400 Bad Request`. If a key was exposed in a log, chat, screenshot, or committed file, revoke it and create a replacement before restarting the API.

### LlamaParse HTTP 499 errors

A 499 during parse-job creation means the upload request closed before LlamaParse returned the job ID. The application now allows the upload request to run for the configured job timeout. For large PDFs, add this to `.env` and restart the API:

```dotenv
LLAMA_PARSE_REQUEST_TIMEOUT_SECONDS=300
LLAMA_PARSE_JOB_TIMEOUT_SECONDS=900
LLAMA_PARSE_MAX_WAIT_SECONDS=1800
```

Retry the upload after restarting Uvicorn. Do not submit the same file repeatedly while a prior parse request may still be running.



---------DELETE ALL DATA------

TRUNCATE TABLE
    citations,
    query_logs,
    knowledge_graph,
    relationships,
    entities,
    chunk_embeddings,
    semantic_chunks,
    sections,
    document_versions,
    ingestion_logs,
    documents
RESTART IDENTITY
CASCADE;


### Retrieval stack

Retrieval uses query expansion, vector search, BM25 keyword search, parent-child section expansion, and the `cross-encoder/ms-marco-MiniLM-L-6-v2` reranker from sentence-transformers. Install dependencies again after updating `pyproject.toml`:

```powershell
pip install -e ".[dev]"
```

## Client local service deployment

Generated service and shortcut templates are available in [`deployment/`](deployment/). They support Windows scheduled-service startup, Linux systemd startup, and macOS launchd startup. See [`deployment/README.md`](deployment/README.md).

## Application URLs

When running locally:

- **User search:** [http://localhost:8000/user](http://localhost:8000/user)
- **Admin workspace:** [http://localhost:8000/admin](http://localhost:8000/admin)

The user URL provides answer-only document search. The admin URL provides authentication, document upload, ingestion monitoring, validation, diagnostics, and document deletion. Replace `localhost:8000` with the client server hostname and port for internal-network deployments.
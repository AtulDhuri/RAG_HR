# Knowledge Intelligence Engine
## Cross-platform installation and client deployment guide

This guide applies to Windows, macOS, and Linux. Docker is not required.

## 1. What the installation provides

The application runs locally on the client machine and provides:

- Browser-based document upload
- LlamaParse-only document ingestion
- Heading-aware chunking
- OpenAI embeddings
- OpenAI query understanding
- Hybrid vector and BM25 retrieval
- Parent-child retrieval
- Cross-encoder reranking
- Grounded answers using indexed document evidence only
- Source citations and page references
- Knowledge-base validation reports
- Authenticated document deletion

The application requires network access to Llama Cloud and OpenAI unless the client has an approved private endpoint arrangement. Uploaded content is sent to LlamaParse and OpenAI according to the configured providers and the client’s policies.

## 2. System requirements

### Required

- 64-bit Windows, macOS, or Linux
- Python 3.12 or newer
- Supabase Cloud PostgreSQL with the `pgvector` extension
Supabase Cloud must be reachable over HTTPS/database TLS from the client machine. The application machine does not need a local PostgreSQL service.- At least 4 GB RAM for normal use
- Additional disk space for uploaded files and the PostgreSQL database

### Recommended

- 8 GB or more RAM
- SSD storage
- A dedicated PostgreSQL database user
- A dedicated machine or internal server for multi-user access
- HTTPS when accessed by other machines

Docker is optional. This guide uses native PostgreSQL.

## 3. Configure Supabase Cloud

The client uses Supabase Cloud instead of local PostgreSQL.

1. Create or receive a Supabase project owned by the client.
2. Open the Supabase SQL Editor.
3. Run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

4. Create a dedicated database password and keep it in the client secret manager.
5. Set `DATABASE_URL` to the Supabase direct connection or pooler connection.

Examples:

```dotenv
# Direct connection
DATABASE_URL=postgresql+psycopg://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require

# Supavisor transaction pooler
DATABASE_URL=postgresql+psycopg://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
```

The application disables psycopg automatic prepared statements for compatibility with Supabase transaction pooling.

Apply migrations from the client application machine after configuring `.env`:

```text
python -m alembic upgrade head
```

The application stores uploaded originals locally, while chunks and embeddings are stored in Supabase PostgreSQL with pgvector.

## 4. Install Python

Install Python 3.12 or newer from the official Python distribution for Windows, macOS, or Linux.

Verify the installation:

```text
python --version
```

Some Linux and macOS systems use `python3` instead:

```text
python3 --version
```

Use the command that reports Python 3.12 or newer for the remaining steps.

## 5. Copy the application

Copy the project directory to the client machine using an approved method.

Do not copy:

- Your existing `.env` file
- Your `.venv` directory
- Existing private database dumps
- Existing API keys
- Existing uploaded client or test files

Create a clean `.env` file on the client machine.

## 6. Create the Python environment

From the project root, create and activate a virtual environment.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run the following as an administrator or use the Python executable directly without activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Windows Command Prompt

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Upgrade packaging tools:

```text
python -m pip install --upgrade pip setuptools wheel
```

Install the application:

```text
python -m pip install -e ".[dev]"
```

The installation includes the CrossEncoder reranker dependency.

## 7. Configure environment variables

Create `.env` in the project root. Use client-owned credentials.

```dotenv
APP_NAME=Knowledge Intelligence Engine
ENVIRONMENT=client
DATABASE_URL=postgresql+psycopg://knowledge_app:replace-password@localhost:5432/knowledge
STORAGE_DIR=./data/uploads
DEFAULT_TENANT_ID=client
AUTO_CREATE_SCHEMA=false

LLAMA_CLOUD_API_KEY=replace-with-client-llamaparse-key
LLAMA_PARSE_ENABLED=true
LLAMA_PARSE_FALLBACK_LOCAL=false
LLAMA_PARSE_TIER=agentic
LLAMA_PARSE_VERSION=latest

OPENAI_API_KEY=replace-with-client-openai-key
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=384

ADMIN_USERNAME=replace-with-client-admin
ADMIN_PASSWORD=replace-with-a-long-random-password
```

Never commit or send `.env` by email or chat. Store secrets using the client’s approved secret-management process.

## 8. Apply the database schema

From the project root, with the virtual environment active:

```text
python -m alembic upgrade head
```

This creates or updates the application tables. It does not upload or delete documents.

## 9. Start the application

### Local-only access

Use this when the application is accessed only on the same machine:

```text
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

### Internal-network access

Use this only when approved by the client’s IT team:

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open the application from another approved machine using:

```text
http://SERVER_HOSTNAME_OR_IP:8000
```

For shared access, place the application behind the client’s HTTPS reverse proxy. Do not expose the development Uvicorn server directly to the public internet.

## 10. First login and document test

1. Open the application URL.
2. Select **Admin ingestion**.
3. Sign in with `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`.
4. Upload a representative PDF.
5. Wait until the document status is `completed`.
6. Confirm the status does not show an extraction warning or failure.
7. Ask a question whose answer is explicitly present in the PDF.
8. Check the source document, section, page, chunk ID, and confidence.

Only LlamaParse is used for ingestion. If LlamaParse is disabled, unavailable, or fails, the document fails visibly rather than falling back to a local parser.

## 11. Verify the reranker

Run a query and open the query diagnostics panel. Confirm that the retrieval diagnostics report:

```text
reranker_backend: cross_encoder
```

If the report says `heuristic_fallback`, install the package again and check that the client machine can access the model repository:

```text
python -m pip install -e ".[dev]"
```

The CrossEncoder model may download the first time it is used.

## 12. Run the knowledge-base validation

After uploading the client documents:

1. Sign in as an administrator.
2. Click **Validate knowledge base**.
3. Review extraction, chunk, embedding, retrieval, answer, citation, and hallucination scores.
4. Review the generated questions and top retrieval failures.
5. Resolve failures before production use.

The system must not be labelled production-ready when the report says:

```text
Knowledge Base Requires Attention
```

The recommended minimum overall health score is 85%.

## 13. Test document deletion

The Admin UI lists uploaded documents. Each document has a **Delete** button.

Deleting a document removes:

- The document database row
- Document versions
- Sections
- Chunks
- Embeddings
- Ingestion logs
- Knowledge graph facts
- Citations
- The uploaded file from storage

Test deletion with a non-critical test document before loading production content.

## 14. Optional API smoke test

Log in through the UI or API, then use the returned bearer token for admin endpoints.

Login request:

```http
POST /api/v1/auth/login
Content-Type: application/json

{"username":"client-admin","password":"client-password"}
```

Validation request:

```http
POST /api/v1/admin/validate
Authorization: Bearer <access-token>
```

Health request:

```http
GET /health
```

## 15. Running as a service

For a client pilot, running the application manually is acceptable. For a persistent deployment, use the operating system’s service manager:

- Windows Service or an approved process manager
- macOS launchd
- Linux systemd or an approved process manager

The service should:

- Start after PostgreSQL is available
- Use the project virtual environment
- Load `.env` securely
- Write logs to an approved location
- Restart after failure
- Run under a restricted operating-system account

Do not run the application as an operating-system administrator or root unless required by the client’s platform policy.

## 16. Backup and recovery

Back up both:

1. PostgreSQL data, including chunks and embeddings.
2. The `STORAGE_DIR` uploaded files.

A database backup without uploaded originals is incomplete. An uploaded-file backup without the database cannot restore the indexed knowledge base.

Test restoring both backups before the client accepts the deployment.

## 17. Troubleshooting

### Database connection failure

Check:

- PostgreSQL is running
- `DATABASE_URL` is correct
- The database user has access
- The `vector` extension exists
- Firewall rules allow the connection

### Duplicate prepared statement error

The application disables psycopg automatic prepared statements for compatibility with transaction poolers. Restart the application after changing the database configuration.

### Document is marked failed

Open the document status and inspect the error message. Common causes include:

- Missing `LLAMA_CLOUD_API_KEY`
- LlamaParse timeout
- LlamaParse authorization failure
- LlamaParse returned no readable content
- Unsupported or corrupted file
- Network restrictions

### No answer is returned

Open query diagnostics and inspect:

- Expanded search terms
- Vector results
- BM25 results
- Reranker score
- Evidence found
- Failure reason

The system intentionally refuses to answer when the indexed document evidence is insufficient.

### Reranker fallback is shown

Confirm that `sentence-transformers` is installed and that the client machine can download the configured CrossEncoder model. The application remains functional, but the strong model-backed reranker is not active until this is resolved.

## 18. Client handover checklist

- [ ] Client-owned `.env` created
- [ ] Existing secrets not copied
- [ ] API keys approved and configured
- [ ] Strong admin credentials configured
- [ ] Supabase Cloud project created
- [ ] Supabase `vector` extension enabled
- [ ] Supabase database connection tested
- [ ] Alembic migrations applied
- [ ] LlamaParse-only ingestion verified
- [ ] OpenAI query understanding verified
- [ ] CrossEncoder reranker verified
- [ ] Representative client PDFs tested
- [ ] Scanned PDF tested
- [ ] Table-containing PDF tested
- [ ] Failed-document behavior tested
- [ ] Delete-document behavior tested
- [ ] Validation report reviewed
- [ ] Overall health score is at least 85%
- [ ] Backups configured
- [ ] Restore procedure tested
- [ ] HTTPS configured for network access
- [ ] Client users trained

## 19. Support information to provide to the client

Provide the client with:

- Application URL
- Admin contact
- User instructions
- Supported file types
- Maximum upload size
- Expected processing time
- Data retention policy
- Backup policy
- External provider disclosure for LlamaParse and OpenAI
- Incident and support process

## Application URLs

After starting the API, use these separate interfaces:

- **User search:** `http://localhost:8000/user`
- **Admin workspace:** `http://localhost:8000/admin`
- **API documentation:** `http://localhost:8000/docs`

For an internal-network deployment, replace `localhost:8000` with the approved client server hostname and port, for example `http://knowledge-server:8000/user` and `http://knowledge-server:8000/admin`.

The user interface displays only grounded answers. The admin interface requires administrator login and provides document upload, ingestion status, validation, retrieval diagnostics, source inspection, and document deletion.

## Automatic local startup

The generated `deployment/` bundle provides automatic local startup templates so the client does not need to run terminal commands each day. PostgreSQL is hosted by Supabase Cloud; only the API service and uploaded-file storage run on the client machine.

- Windows: `deployment/windows/install.ps1` registers the API with Windows Task Scheduler and creates desktop URL shortcuts.
- Linux: `deployment/linux/install.sh` installs and enables a systemd service.
- macOS: `deployment/macos/com.knowledgebase.api.plist` provides a launchd service template.

See [`deployment/README.md`](deployment/README.md) for service installation details. PostgreSQL must also be installed and configured as an operating-system service by the client’s approved PostgreSQL installer or IT process.

### Retrieval performance indexes

Run the latest migrations after deployment. Migration `0004_retrieval_indexes` adds a GIN full-text index and an HNSW pgvector index in Supabase Cloud. These indexes are required for fast retrieval on larger document collections:

```text
python -m alembic upgrade head
```

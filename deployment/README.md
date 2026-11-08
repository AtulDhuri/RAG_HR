# Client deployment bundle

This folder contains generated service and shortcut templates for a local client deployment.

## Layout

```text
Client machine
├── Knowledge Base API service
├── Uploaded document storage
└── Browser shortcuts
    ├── User Search
    └── Admin Workspace

Supabase Cloud
├── PostgreSQL database
├── pgvector extension
└── Knowledge Base tables, chunks, and embeddings
```

The client uses Supabase Cloud for PostgreSQL and pgvector. PostgreSQL does not need to be installed on the client machine. The client machine runs the Knowledge Base API, stores uploaded originals locally, and provides browser shortcuts.

## Windows

Run PowerShell as the client deployment administrator:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deployment\windows\install.ps1 -ProjectRoot "C:\Program Files\KnowledgeBase"
```

The Windows installer creates the virtual environment, installs the application, applies migrations against Supabase Cloud, creates local storage, creates User Search and Admin Workspace shortcuts, and registers an automatic-start scheduled task for the API. The task behaves as a local background service without requiring the client to run a terminal.

## Linux

Copy the project to `/opt/knowledge-base`, configure `.env` with the client’s Supabase Cloud connection, then install the systemd unit:

```bash
sudo ./deployment/linux/install.sh /opt/knowledge-base
```

## macOS

Copy the project to `/Applications/KnowledgeBase` or another approved location, install PostgreSQL/pgvector, configure `.env`, then copy and customize `deployment/macos/com.knowledgebase.api.plist` for the client user.

## URLs

- User Search: `http://127.0.0.1:8000/user`
- Admin Workspace: `http://127.0.0.1:8000/admin`

The application still requires outbound HTTPS access to LlamaParse and OpenAI unless the client has approved private endpoints.

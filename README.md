# DocMind

DocMind is a private PDF research workspace: upload PDFs, have them processed in a background worker, ask questions across selected documents, and open validated source citations at the original page.

## Start with Docker

Requirements: Docker Desktop with Compose, an OpenAI-compatible API key, and about 2 GB available memory.

```powershell
Copy-Item .env.example .env
# Edit .env: set a random SECRET_KEY and LLM_API_KEY
docker compose up --build
```

Open [http://localhost:8080](http://localhost:8080). The API documentation is at [http://localhost:8000/docs](http://localhost:8000/docs). PostgreSQL and Redis data and uploaded PDFs use named volumes. `docker compose down` preserves them; `docker compose down -v` removes all stored data.

For production, set `COOKIE_SECURE=true`, use a strong unique `SECRET_KEY` and database password, serve behind HTTPS, restrict `FRONTEND_ORIGINS`, and use a managed PostgreSQL/Redis service. Do not commit `.env`.

## Local development

1. Install Python 3.12+, Node 22+, PostgreSQL 16 with pgvector, and Redis 7.
2. Copy `.env.example` to `.env`, then set local database/Redis URLs and the model API key.
3. Python: `pip install -e '.[dev]'`
4. Frontend: `cd frontend; npm install`
5. Run the schema migration: `alembic upgrade head`
6. In separate terminals run `uvicorn backend.app.main:app --reload`, `celery -A backend.app.tasks:celery_app worker --loglevel=INFO`, and `cd frontend; npm run dev`.

## How the PDF to answer flow works

1. The API validates the extension, PDF signature and configured byte limit, computes a SHA-256 digest, and stores the file under a random private storage key. Duplicate uploads by the same user reuse the existing record. Storage uses a private local adapter by default or a private S3-compatible bucket when `S3_BUCKET` is set; no public URL is returned.
2. After the database commit, a Celery job extracts selectable text with PyMuPDF, retains one-based page references, chunks the text with overlap, requests configurable embeddings, and stores vectors in pgvector. Failures and scanned PDFs without text are reflected in document status and error details.
3. Chat retrieval enforces document ownership and embedding model compatibility. It combines cosine-distance vector search and PostgreSQL full-text ranking with reciprocal rank fusion.
4. The configured chat model receives retrieved excerpts as untrusted quoted data. The streaming endpoint returns server-sent events; after completion the API validates generated source IDs against retrieved chunks and persists citation metadata only for accepted IDs. Clicking a citation opens the authenticated private PDF route at its one-based page.

## Configuration

See `.env.example`. `LLM_BASE_URL`, `LLM_MODEL`, `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` select an OpenAI-compatible provider. PostgreSQL vector column size is fixed at migration/model creation from `EMBEDDING_DIMENSIONS`; changing dimensions requires a migration and re-embedding existing chunks. `MAX_UPLOAD_MB`, `MAX_PDF_PAGES`, `CHUNK_TOKENS` and `CHUNK_OVERLAP` tune ingestion. Without `S3_BUCKET`, files live on a private shared local volume; keep API and worker on the same volume.

## Checks

```bash
ruff check backend
pytest
cd frontend && npm run build
docker compose config
```

Tests cover deterministic chunk/page behavior, real PyMuPDF extraction and chunk persistence against test doubles, citation validation, storage path isolation, token helpers, and the health route. They do not exercise a real PostgreSQL/pgvector + Redis + model request chain. That end-to-end check needs those services, a valid embedding/chat provider key, and Docker.

## Current scope and follow-up

The implemented slice includes registration/login, Argon2 password hashing, short-lived JWT access credentials, rotating/revocable HTTP-only refresh cookies, rate limits for public auth and uploads, private PDF upload/list/rename/delete/retry/preview, local or S3-compatible storage, collections and collection-scoped retrieval, async ingestion, hybrid retrieval, persisted conversations, SSE answers with verified source citations, and a responsive light/dark UI. This starter does not yet include email verification or reset delivery, account deletion, OCR, PDF text-coordinate highlights, upload progress, or conversation deletion. It reports scanned PDFs as requiring OCR. The live PostgreSQL/Redis/model workflow still needs an integration run before production use.

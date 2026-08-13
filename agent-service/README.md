# Meeting Agent Service (Day 4)

This module provides the internal Day 4 scheduling runtime: a structured Pydantic state graph
with exactly one Supervisor and Requirement, Policy, and Scheduling specialist agents. It uses a
replaceable OpenAI-compatible DeepSeek provider or deterministic fixture provider, Java read-only
tools, Qdrant-backed policy citations, and persisted run/step/tool-call trace metadata.

The service is internal-only. Java authenticates the user and proxies SSE; Python validates the
Java-issued context token and never reads Java business tables directly. When a DeepSeek key is
not configured, `/internal/v1/health` remains HTTP 200 with `status=DEGRADED`.

Day 5 capabilities (OR-Tools optimization, HITL confirmations, checkpoints, business-result
callbacks, and booking write tools) are intentionally not implemented here yet.

Required runtime environment variables:

- `AGENT_DATABASE_URL` (for example, a `mysql+pymysql://.../meeting_agent` URL supplied by Compose)
- `APP_TIMEZONE=Asia/Shanghai`
- `AGENT_MODEL_PROVIDER=fixture|deepseek` (the Compose default is `fixture`)
- `DEEPSEEK_API_KEY` (required only with `AGENT_MODEL_PROVIDER=deepseek`)
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `INTERNAL_SERVICE_TOKEN`
- `AGENT_CONTEXT_JWT_SECRET`
- `AGENT_CONTEXT_AUDIENCE`
- `BUSINESS_SERVICE_BASE_URL`
- `QDRANT_URL`
- `QDRANT_COLLECTION` (production default: `meeting_policies_bge_m3_v1`)
- `RAG_SOURCE_DIR` (Compose defaults to `/app/rag-documents`; Markdown and text PDF only)
- `RAG_EMBEDDING_PROVIDER=bge_m3`
- `BGE_M3_HOST_PATH` (Compose host path; for this workspace: `D:/rag001/bge-m3`)
- `RAG_EMBEDDING_MODEL_PATH=/models/bge-m3`
- `RAG_EMBEDDING_DEVICE=cpu`
- `RAG_EMBEDDING_BATCH_SIZE=4`
- `RAG_EMBEDDING_MAX_LENGTH=2048`

## RAG document ingestion

The Compose `rag-init` one-shot service validates metadata, chunks Markdown/text PDFs,
deduplicates normalized content by SHA-256, registers `rag_document`, and replaces each
document's Qdrant points idempotently. Production ingestion, admin mutations, and policy queries
share a process-cached local BGE-M3 dense provider (1024 dimensions); the API preloads it before
reporting startup complete. The browsable source retains
the `RAG 测试问题` section, but that section is excluded from Qdrant. Runtime retrieval does not
inject built-in seed chunks. It does not perform OCR, hybrid sparse retrieval, reranking, or
directory-mirror deletion.

Run the importer directly after applying Alembic migrations:

```powershell
$env:RAG_EMBEDDING_PROVIDER="bge_m3"
$env:RAG_EMBEDDING_MODEL_PATH="D:\rag001\bge-m3"
uv run python -m app.rag.ingest --source-dir ..\deploy\rag-documents
```
- `LOG_LEVEL`

Quality gates:

```powershell
uv sync --frozen --group dev
uv run ruff check .
uv run mypy app
uv run pytest
```

The container command runs `alembic upgrade head` before Uvicorn. A migration failure therefore
prevents the API process from starting.

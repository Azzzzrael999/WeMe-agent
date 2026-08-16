# WeMe Agent Service

This module is WeMe's internal Python runtime. It implements the fixed Supervisor,
Requirement, Policy, and Scheduling agents; a bounded Plan/Act/Observe/Verify/Replan loop;
OpenAI-compatible DeepSeek tool calling; OR-Tools Top 3 scheduling with an independent hard-
constraint validator; Qdrant-backed policy retrieval; Redis checkpoints; CREATE/RESCHEDULE/
CANCEL HITL; asynchronous booking-result recovery; post-meeting draft extraction; and layered
agent evaluation.

The service is internal-only. Browsers call Java, Java proxies Agent SSE and signs a short-lived
AgentContextToken, and Python accesses business facts only through the Java Tool API. Python never
reads or writes Java business tables. If DeepSeek is not configured, the health endpoint remains
HTTP 200 with `status=DEGRADED`; database or checkpoint failures return an unhealthy response.

## Runtime configuration

All runtime values are supplied through environment variables. Secrets must not be committed.

- `AGENT_DATABASE_URL`: SQLAlchemy URL for the isolated `meeting_agent` database.
- `APP_TIMEZONE`: must be `Asia/Shanghai`.
- `AGENT_MODEL_PROVIDER`: `fixture` or `deepseek`; Compose defaults to `fixture` so the stack can
  start without a model key, while live-model runs explicitly select `deepseek`.
- `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`: DeepSeek provider settings.
- `MODEL_TIMEOUT_SECONDS`, `MODEL_MAX_RETRIES`: bounded model timeout and network retries.
- `BUSINESS_SERVICE_URL`: internal Java service base URL.
- `INTERNAL_SERVICE_TOKEN`, `AGENT_CONTEXT_JWT_SECRET`, `AGENT_CONTEXT_AUDIENCE`: internal call
  authentication. Production values must be non-empty secrets supplied by deployment.
- `AGENT_CHECKPOINT_REDIS_URL`, `AGENT_CHECKPOINT_TTL_SECONDS`: isolated Redis checkpoint storage.
- `AGENT_MAX_MODEL_CALLS`, `AGENT_MAX_TOOL_CALLS`, `AGENT_MAX_GRAPH_NODES`: run budgets.
- `QDRANT_URL`, `QDRANT_COLLECTION`: policy vector store; the production collection is
  `meeting_policies_bge_m3_v1`.
- `RAG_EMBEDDING_PROVIDER`: `bge_m3` in production and `deterministic` only for tests/fixtures.
- `RAG_EMBEDDING_MODEL_PATH`, `RAG_EMBEDDING_DEVICE`, `RAG_EMBEDDING_BATCH_SIZE`,
  `RAG_EMBEDDING_MAX_LENGTH`: local BGE-M3 settings.
- `RAG_EMBEDDING_TIMEOUT_SECONDS`, `RAG_KEEPALIVE_INTERVAL_SECONDS`, `RAG_QUERY_CACHE_SIZE`,
  `RAG_QUERY_CACHE_TTL_SECONDS`: bounded retrieval latency and warm-cache settings.
- `RAG_SOURCE_DIR`: seed document directory used by the importer.
- `LOG_LEVEL`: `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`.

`FIXTURE_NOW` controls the deterministic fixture clock and is intended for component evaluation
and local offline smoke only. The Compose host model mount (`BGE_M3_HOST_PATH`) is deployment
configuration rather than an Agent process environment variable.

## RAG document ingestion

The `rag-init` one-shot service validates controlled metadata, chunks Markdown and text-based PDF
documents, deduplicates normalized content by SHA-256, records `rag_document`, and replaces each
document's Qdrant points idempotently. Production ingestion, administrator mutations, and policy
queries share the process-cached local BGE-M3 dense provider (1024 dimensions). Runtime retrieval
does not inject built-in seed chunks and does not perform OCR, sparse hybrid retrieval, reranking,
or directory-mirror deletion.

After applying Alembic migrations, the importer can also be run directly:

```powershell
$env:RAG_EMBEDDING_PROVIDER="bge_m3"
$env:RAG_EMBEDDING_MODEL_PATH="D:\rag001\bge-m3"
uv run python -m app.rag.ingest --source-dir ..\deploy\rag-documents
```

## Verification

```powershell
uv sync --frozen --group dev
uv run ruff check . ..\scripts
uv run mypy app
uv run pytest
uv run pytest ..\scripts\test_build_agent_evaluation_report.py ..\scripts\test_demo_two_scenarios.py
```

Run the deterministic component evaluation with `uv run python -m app.evaluation`. This report is
explicitly `component-fixture`; it is not a live-model or end-to-end result. Live-model and product
trajectory reports are produced by the repository-level evaluation scripts.

The container starts with `alembic upgrade head` and then Uvicorn. A migration failure prevents
the API process from starting.

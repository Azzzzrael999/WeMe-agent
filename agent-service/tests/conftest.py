import os

os.environ.setdefault("AGENT_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("APP_TIMEZONE", "Asia/Shanghai")
os.environ.setdefault("DEEPSEEK_BASE_URL", "https://api.example.invalid")
os.environ.setdefault("DEEPSEEK_MODEL", "test-model")
os.environ.setdefault("AGENT_MODEL_PROVIDER", "fixture")
os.environ.setdefault("RAG_EMBEDDING_PROVIDER", "deterministic")
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "test-service-token")
os.environ.setdefault(
    "AGENT_CONTEXT_JWT_SECRET", "test-agent-context-secret-with-at-least-32-bytes"
)
os.environ.setdefault("AGENT_CONTEXT_AUDIENCE", "agent-service")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def timestamp_column(*, nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=nullable,
        # Alembic owns the production MySQL(3) DDL.  The portable model default
        # keeps metadata repository tests runnable on SQLite without changing
        # deployed schema semantics.
        server_default=None if nullable else text("CURRENT_TIMESTAMP"),
    )


class AgentThread(Base):
    __tablename__ = "agent_thread"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column()


class AgentRun(Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        Index("idx_agent_run_user_thread_created", "user_id", "thread_id", "created_at"),
        Index("idx_agent_run_user_status_created", "user_id", "status", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    question_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    answer_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_hit_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_miss_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    model_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    configured_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_models: Mapped[list[str]] = mapped_column(mysql.JSON, nullable=False, default=list)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = timestamp_column()
    finished_at: Mapped[datetime | None] = timestamp_column(nullable=True)


class AgentMessage(Base):
    __tablename__ = "agent_message"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence_no", name="uk_agent_message_thread_sequence"),
        UniqueConstraint(
            "user_id",
            "client_request_id",
            "role",
            name="uk_agent_message_user_request_role",
        ),
        Index("idx_agent_message_user_thread_created", "user_id", "thread_id", "created_at"),
    )

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    visible_payload: Mapped[dict[str, object]] = mapped_column(
        mysql.JSON, nullable=False, default=dict
    )
    client_request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = timestamp_column()


class AgentStep(Base):
    __tablename__ = "agent_step"
    __table_args__ = (UniqueConstraint("run_id", "sequence_no", name="uk_agent_step_run_sequence"),)

    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_summary: Mapped[str] = mapped_column(Text, nullable=False)
    output_summary: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = timestamp_column()


class AgentLoopEvent(Base):
    __tablename__ = "agent_loop_event"
    __table_args__ = (UniqueConstraint("run_id", "sequence_no", name="uk_agent_loop_run_sequence"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(128), nullable=False)
    feedback_codes: Mapped[list[str]] = mapped_column(mysql.JSON, nullable=False)
    replan_count: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_model_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = timestamp_column()


class AgentToolCall(Base):
    __tablename__ = "agent_tool_call"

    tool_call_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    sanitized_args: Mapped[dict[str, object]] = mapped_column(mysql.JSON, nullable=False)
    result_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = timestamp_column()


class UserSchedulingPreference(Base):
    __tablename__ = "user_scheduling_preference"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    preferences_json: Mapped[dict[str, object]] = mapped_column(mysql.JSON, nullable=False)
    updated_from_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = timestamp_column()


class AgentBusinessEvent(Base):
    __tablename__ = "agent_business_event"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_no: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(mysql.JSON, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=3), nullable=False)


class RagDocument(Base):
    __tablename__ = "rag_document"
    __table_args__ = (UniqueConstraint("checksum", name="uk_rag_document_checksum"),)

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    department: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column()
    indexed_at: Mapped[datetime | None] = timestamp_column(nullable=True)
    deleted_at: Mapped[datetime | None] = timestamp_column(nullable=True)

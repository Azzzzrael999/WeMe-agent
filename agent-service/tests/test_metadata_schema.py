from sqlalchemy import UniqueConstraint

import app.models  # noqa: F401
from app.database.base import Base


def test_day_one_metadata_columns_match_data_contract() -> None:
    expected_columns = {
        "agent_thread": {"thread_id", "user_id", "title", "created_at", "updated_at"},
        "agent_run": {
            "run_id",
            "thread_id",
            "trace_id",
            "user_id",
            "intent",
            "status",
            "question_summary",
            "answer_summary",
            "model_call_count",
            "tool_call_count",
            "input_tokens",
            "output_tokens",
            "cache_hit_tokens",
            "cache_miss_tokens",
            "model_provider",
            "configured_model",
            "response_models",
            "prompt_version",
            "schema_version",
            "duration_ms",
            "error_code",
            "created_at",
            "finished_at",
        },
        "agent_step": {
            "step_id",
            "run_id",
            "sequence_no",
            "agent_name",
            "node_name",
            "status",
            "input_summary",
            "output_summary",
            "duration_ms",
            "error_code",
            "created_at",
        },
        "agent_loop_event": {
            "id",
            "run_id",
            "sequence_no",
            "phase",
            "iteration",
            "decision",
            "feedback_codes",
            "replan_count",
            "remaining_model_calls",
            "remaining_tool_calls",
            "stop_reason",
            "created_at",
        },
        "agent_tool_call": {
            "tool_call_id",
            "run_id",
            "tool_name",
            "risk_level",
            "sanitized_args",
            "result_summary",
            "status",
            "duration_ms",
            "created_at",
        },
        "user_scheduling_preference": {
            "user_id",
            "preferences_json",
            "updated_from_run_id",
            "updated_at",
        },
        "agent_business_event": {
            "event_id",
            "run_id",
            "request_no",
            "event_type",
            "payload_json",
            "processed_at",
        },
        "rag_document": {
            "document_id",
            "title",
            "document_type",
            "department",
            "effective_date",
            "priority",
            "source_path",
            "file_name",
            "media_type",
            "content_text",
            "version",
            "checksum",
            "status",
            "chunk_count",
            "record_version",
            "created_at",
            "updated_at",
            "indexed_at",
            "deleted_at",
        },
    }

    assert set(Base.metadata.tables) == set(expected_columns)
    for table_name, columns in expected_columns.items():
        assert set(Base.metadata.tables[table_name].columns.keys()) == columns


def test_preference_user_id_is_not_generated_by_python_database() -> None:
    user_id = Base.metadata.tables["user_scheduling_preference"].c.user_id

    assert user_id.primary_key is True
    assert user_id.autoincrement is False


def test_rag_document_checksum_is_unique() -> None:
    table = Base.metadata.tables["rag_document"]
    unique_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("checksum",) in unique_sets

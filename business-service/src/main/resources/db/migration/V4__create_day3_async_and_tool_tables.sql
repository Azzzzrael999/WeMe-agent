CREATE TABLE booking_draft (
    id BIGINT NOT NULL AUTO_INCREMENT,
    confirmation_token VARCHAR(80) NOT NULL,
    user_id BIGINT NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    tool_call_id VARCHAR(80) NOT NULL,
    operation VARCHAR(24) NOT NULL,
    payload_json JSON NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL,
    version INT NOT NULL DEFAULT 0,
    expires_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    used_at DATETIME(3) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_booking_draft_confirmation_token UNIQUE (confirmation_token)
);

CREATE INDEX idx_booking_draft_user_status
    ON booking_draft (user_id, status, expires_at);

CREATE TABLE booking_request (
    id BIGINT NOT NULL AUTO_INCREMENT,
    request_no VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    trace_id VARCHAR(64) NOT NULL,
    tool_call_id VARCHAR(80) NOT NULL,
    operation VARCHAR(24) NOT NULL,
    payload_json JSON NOT NULL,
    status VARCHAR(24) NOT NULL,
    meeting_id BIGINT NULL,
    error_code VARCHAR(64) NULL,
    error_message VARCHAR(255) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_booking_request_no UNIQUE (request_no)
);

CREATE INDEX idx_booking_request_user_created
    ON booking_request (user_id, created_at);
CREATE INDEX idx_booking_request_run_id
    ON booking_request (run_id);

CREATE TABLE message_outbox (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    aggregate_type VARCHAR(64) NOT NULL,
    aggregate_id VARCHAR(64) NOT NULL,
    topic VARCHAR(128) NOT NULL,
    tag VARCHAR(64) NOT NULL,
    trace_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NULL,
    payload_json JSON NOT NULL,
    status VARCHAR(16) NOT NULL,
    retry_count INT NOT NULL DEFAULT 0,
    next_retry_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    sent_at DATETIME(3) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_message_outbox_event_id UNIQUE (event_id)
);

CREATE INDEX idx_message_outbox_publish
    ON message_outbox (status, next_retry_at, id);

CREATE TABLE event_consume_record (
    id BIGINT NOT NULL AUTO_INCREMENT,
    consumer_group VARCHAR(128) NOT NULL,
    event_id VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    consumed_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_event_consume_record UNIQUE (consumer_group, event_id)
);

CREATE TABLE notification (
    id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    type VARCHAR(32) NOT NULL,
    title VARCHAR(128) NOT NULL,
    content VARCHAR(1000) NOT NULL,
    related_meeting_id BIGINT NULL,
    read_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX idx_notification_user_created
    ON notification (user_id, created_at);

CREATE TABLE agent_tool_audit (
    id BIGINT NOT NULL AUTO_INCREMENT,
    trace_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    tool_call_id VARCHAR(80) NOT NULL,
    tool_name VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    risk_level VARCHAR(16) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    result_code VARCHAR(64) NOT NULL,
    response_json JSON NULL,
    duration_ms BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_tool_audit UNIQUE (run_id, tool_call_id, tool_name)
);

CREATE INDEX idx_agent_tool_audit_user_created
    ON agent_tool_audit (user_id, created_at);

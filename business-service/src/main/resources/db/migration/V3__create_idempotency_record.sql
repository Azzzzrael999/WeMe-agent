CREATE TABLE idempotency_record (
    id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    operation VARCHAR(48) NOT NULL,
    idempotency_key VARCHAR(80) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL,
    response_json JSON NULL,
    expires_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_idempotency_record
        UNIQUE (user_id, operation, idempotency_key),
    CONSTRAINT fk_idempotency_record_user
        FOREIGN KEY (user_id) REFERENCES sys_user (id)
);

CREATE INDEX idx_idempotency_record_expires_at
    ON idempotency_record (expires_at);

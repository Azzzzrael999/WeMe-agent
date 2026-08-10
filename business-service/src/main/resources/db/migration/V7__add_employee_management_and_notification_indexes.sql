ALTER TABLE sys_user ADD COLUMN version INT NOT NULL DEFAULT 0;

UPDATE sys_user SET status = 'DISABLED' WHERE status = 'INACTIVE';

ALTER TABLE sys_user
    ADD CONSTRAINT uq_sys_user_email UNIQUE (email);

CREATE INDEX idx_notification_user_read_created
    ON notification (user_id, read_at, created_at);

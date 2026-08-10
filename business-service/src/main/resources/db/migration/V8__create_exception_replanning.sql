CREATE TABLE meeting_replan_case (
    id BIGINT NOT NULL AUTO_INCREMENT,
    case_no VARCHAR(40) NOT NULL,
    meeting_id BIGINT NOT NULL,
    organizer_id BIGINT NOT NULL,
    failed_room_id BIGINT NOT NULL,
    failed_room_name VARCHAR(64) NOT NULL,
    failure_reason VARCHAR(255) NOT NULL,
    room_status_version INT NOT NULL,
    original_start_at DATETIME(3) NOT NULL,
    original_end_at DATETIME(3) NOT NULL,
    constraint_snapshot JSON NOT NULL,
    status VARCHAR(24) NOT NULL,
    resolution_type VARCHAR(32) NULL,
    resolved_room_id BIGINT NULL,
    resolved_start_at DATETIME(3) NULL,
    resolved_end_at DATETIME(3) NULL,
    version INT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    resolved_at DATETIME(3) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_replan_case_no UNIQUE (case_no),
    CONSTRAINT uq_meeting_replan_event
        UNIQUE (meeting_id, failed_room_id, room_status_version),
    CONSTRAINT fk_replan_case_meeting FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_replan_case_organizer FOREIGN KEY (organizer_id) REFERENCES sys_user (id),
    CONSTRAINT fk_replan_case_failed_room FOREIGN KEY (failed_room_id) REFERENCES meeting_room (id),
    CONSTRAINT fk_replan_case_resolved_room FOREIGN KEY (resolved_room_id) REFERENCES meeting_room (id)
);

CREATE INDEX idx_replan_case_organizer_status_created
    ON meeting_replan_case (organizer_id, status, created_at);
CREATE INDEX idx_replan_case_status_created
    ON meeting_replan_case (status, created_at);
CREATE INDEX idx_replan_case_meeting_id
    ON meeting_replan_case (meeting_id);

ALTER TABLE notification ADD COLUMN related_replan_case_id BIGINT NULL;
CREATE INDEX idx_notification_related_replan_case
    ON notification (related_replan_case_id);

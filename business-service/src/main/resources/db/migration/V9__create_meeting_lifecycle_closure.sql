CREATE TABLE meeting_lifecycle_profile (
    meeting_id BIGINT NOT NULL,
    preparation_version INT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (meeting_id),
    CONSTRAINT fk_lifecycle_profile_meeting
        FOREIGN KEY (meeting_id) REFERENCES meeting (id)
);

CREATE TABLE meeting_agenda_item (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    sequence_no INT NOT NULL,
    topic VARCHAR(200) NOT NULL,
    owner_employee_id BIGINT NOT NULL,
    planned_minutes INT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_agenda_sequence UNIQUE (meeting_id, sequence_no),
    CONSTRAINT fk_meeting_agenda_meeting FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_meeting_agenda_owner FOREIGN KEY (owner_employee_id) REFERENCES sys_user (id),
    CONSTRAINT chk_meeting_agenda_minutes CHECK (planned_minutes BETWEEN 5 AND 240)
);

CREATE INDEX idx_meeting_agenda_owner ON meeting_agenda_item (owner_employee_id);

CREATE TABLE meeting_material (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    sequence_no INT NOT NULL,
    title VARCHAR(200) NOT NULL,
    owner_employee_id BIGINT NOT NULL,
    required BOOLEAN NOT NULL,
    status VARCHAR(16) NOT NULL,
    version_label VARCHAR(64) NULL,
    note VARCHAR(500) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_material_sequence UNIQUE (meeting_id, sequence_no),
    CONSTRAINT fk_meeting_material_meeting FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_meeting_material_owner FOREIGN KEY (owner_employee_id) REFERENCES sys_user (id),
    CONSTRAINT chk_meeting_material_status CHECK (status IN ('MISSING', 'READY'))
);

CREATE INDEX idx_meeting_material_owner ON meeting_material (owner_employee_id);

CREATE TABLE meeting_reminder_delivery (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    meeting_start_at DATETIME(3) NOT NULL,
    recipient_id BIGINT NOT NULL,
    reminder_type VARCHAR(32) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_reminder_delivery
        UNIQUE (meeting_id, meeting_start_at, recipient_id, reminder_type),
    CONSTRAINT fk_meeting_reminder_meeting FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_meeting_reminder_recipient FOREIGN KEY (recipient_id) REFERENCES sys_user (id)
);

CREATE INDEX idx_meeting_reminder_created ON meeting_reminder_delivery (created_at);

CREATE TABLE post_meeting_draft (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    request_id VARCHAR(80) NOT NULL,
    agent_run_id VARCHAR(64) NOT NULL,
    transcript MEDIUMTEXT NOT NULL,
    payload_json MEDIUMTEXT NULL,
    status VARCHAR(24) NOT NULL,
    version INT NOT NULL DEFAULT 0,
    error_code VARCHAR(64) NULL,
    submitted_by BIGINT NOT NULL,
    reviewed_by BIGINT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    reviewed_at DATETIME(3) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_post_meeting_draft_meeting UNIQUE (meeting_id),
    CONSTRAINT uq_post_meeting_draft_request UNIQUE (request_id),
    CONSTRAINT fk_post_meeting_draft_meeting FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_post_meeting_draft_submitter FOREIGN KEY (submitted_by) REFERENCES sys_user (id),
    CONSTRAINT fk_post_meeting_draft_reviewer FOREIGN KEY (reviewed_by) REFERENCES sys_user (id),
    CONSTRAINT chk_post_meeting_draft_status
        CHECK (status IN ('PROCESSING', 'PENDING_REVIEW', 'ACCEPTED', 'REJECTED', 'FAILED'))
);

CREATE INDEX idx_post_meeting_draft_status_updated
    ON post_meeting_draft (status, updated_at);

CREATE TABLE meeting_minutes (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    background VARCHAR(2000) NOT NULL,
    discussion_summary MEDIUMTEXT NOT NULL,
    conclusion VARCHAR(2000) NOT NULL,
    confirmed_by BIGINT NOT NULL,
    confirmed_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_minutes_meeting UNIQUE (meeting_id),
    CONSTRAINT fk_meeting_minutes_meeting FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_meeting_minutes_confirmer FOREIGN KEY (confirmed_by) REFERENCES sys_user (id)
);

CREATE TABLE meeting_decision (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    sequence_no INT NOT NULL,
    content VARCHAR(1000) NOT NULL,
    rationale VARCHAR(1000) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_decision_sequence UNIQUE (meeting_id, sequence_no),
    CONSTRAINT fk_meeting_decision_meeting FOREIGN KEY (meeting_id) REFERENCES meeting (id)
);

CREATE TABLE meeting_action_item (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    sequence_no INT NOT NULL,
    title VARCHAR(200) NOT NULL,
    description VARCHAR(1000) NULL,
    assignee_employee_id BIGINT NOT NULL,
    due_at DATETIME(3) NOT NULL,
    status VARCHAR(24) NOT NULL,
    version INT NOT NULL DEFAULT 0,
    completed_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_action_sequence UNIQUE (meeting_id, sequence_no),
    CONSTRAINT fk_meeting_action_meeting FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_meeting_action_assignee FOREIGN KEY (assignee_employee_id) REFERENCES sys_user (id),
    CONSTRAINT chk_meeting_action_status CHECK (status IN ('OPEN', 'IN_PROGRESS', 'DONE'))
);

CREATE INDEX idx_meeting_action_assignee_due
    ON meeting_action_item (assignee_employee_id, status, due_at);

CREATE TABLE action_item_reminder_delivery (
    id BIGINT NOT NULL AUTO_INCREMENT,
    action_item_id BIGINT NOT NULL,
    due_at DATETIME(3) NOT NULL,
    recipient_id BIGINT NOT NULL,
    reminder_type VARCHAR(32) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_action_item_reminder_delivery
        UNIQUE (action_item_id, due_at, recipient_id, reminder_type),
    CONSTRAINT fk_action_reminder_action FOREIGN KEY (action_item_id) REFERENCES meeting_action_item (id),
    CONSTRAINT fk_action_reminder_recipient FOREIGN KEY (recipient_id) REFERENCES sys_user (id)
);

CREATE INDEX idx_action_reminder_created ON action_item_reminder_delivery (created_at);

INSERT INTO meeting (
    meeting_no, title, meeting_type, organizer_id, room_id, start_at, end_at,
    status, source, version, created_at, updated_at
)
SELECT
    'MTG-DEMO-POST-20260813', '支付网关 V2 上线复盘', 'ARCHITECTURE_REVIEW',
    1001, 101, '2026-08-13 15:00:00.000', '2026-08-13 16:00:00.000',
    'COMPLETED', 'MANUAL', 0, '2026-08-13 14:00:00.000', '2026-08-13 16:00:00.000'
WHERE LOWER('${demo-data-enabled}') = 'true'
  AND NOT EXISTS (
      SELECT 1 FROM meeting WHERE meeting_no = 'MTG-DEMO-POST-20260813'
  );

INSERT INTO meeting_participant (meeting_id, employee_id, participant_type)
SELECT meeting.id, employee.id, 'REQUIRED'
FROM meeting
JOIN sys_user employee ON employee.id IN (1001, 1003)
WHERE meeting.meeting_no = 'MTG-DEMO-POST-20260813'
  AND LOWER('${demo-data-enabled}') = 'true'
  AND NOT EXISTS (
      SELECT 1
      FROM meeting_participant existing
      WHERE existing.meeting_id = meeting.id
        AND existing.employee_id = employee.id
  );

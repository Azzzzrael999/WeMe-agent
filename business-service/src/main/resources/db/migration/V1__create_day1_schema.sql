CREATE TABLE department (
    id BIGINT NOT NULL AUTO_INCREMENT,
    name VARCHAR(64) NOT NULL,
    default_building VARCHAR(64) NOT NULL,
    default_floor VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_department_name UNIQUE (name)
);

CREATE TABLE sys_user (
    id BIGINT NOT NULL AUTO_INCREMENT,
    username VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(64) NOT NULL,
    email VARCHAR(128) NOT NULL,
    department_id BIGINT NULL,
    role VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    CONSTRAINT uq_sys_user_username UNIQUE (username),
    CONSTRAINT fk_sys_user_department
        FOREIGN KEY (department_id) REFERENCES department (id)
);

CREATE INDEX idx_sys_user_department_id ON sys_user (department_id);
CREATE INDEX idx_sys_user_display_name ON sys_user (display_name);

CREATE TABLE meeting_room (
    id BIGINT NOT NULL AUTO_INCREMENT,
    code VARCHAR(32) NOT NULL,
    name VARCHAR(64) NOT NULL,
    building VARCHAR(64) NOT NULL,
    floor VARCHAR(32) NOT NULL,
    capacity INT NOT NULL,
    room_type VARCHAR(32) NOT NULL,
    is_hot BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(16) NOT NULL,
    version INT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_room_code UNIQUE (code),
    CONSTRAINT chk_meeting_room_capacity CHECK (capacity > 0)
);

CREATE TABLE room_feature (
    id BIGINT NOT NULL AUTO_INCREMENT,
    code VARCHAR(32) NOT NULL,
    name VARCHAR(64) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_room_feature_code UNIQUE (code)
);

CREATE TABLE meeting_room_feature (
    room_id BIGINT NOT NULL,
    feature_id BIGINT NOT NULL,
    PRIMARY KEY (room_id, feature_id),
    CONSTRAINT fk_meeting_room_feature_room
        FOREIGN KEY (room_id) REFERENCES meeting_room (id),
    CONSTRAINT fk_meeting_room_feature_feature
        FOREIGN KEY (feature_id) REFERENCES room_feature (id)
);

CREATE INDEX idx_meeting_room_feature_feature_id
    ON meeting_room_feature (feature_id);

CREATE TABLE meeting (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_no VARCHAR(40) NOT NULL,
    title VARCHAR(128) NOT NULL,
    meeting_type VARCHAR(32) NOT NULL,
    organizer_id BIGINT NOT NULL,
    room_id BIGINT NOT NULL,
    start_at DATETIME(3) NOT NULL,
    end_at DATETIME(3) NOT NULL,
    status VARCHAR(24) NOT NULL,
    source VARCHAR(16) NOT NULL,
    run_id VARCHAR(64) NULL,
    request_no VARCHAR(64) NULL,
    version INT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    cancelled_at DATETIME(3) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_no UNIQUE (meeting_no),
    CONSTRAINT fk_meeting_organizer FOREIGN KEY (organizer_id) REFERENCES sys_user (id),
    CONSTRAINT fk_meeting_room FOREIGN KEY (room_id) REFERENCES meeting_room (id),
    CONSTRAINT chk_meeting_time_range CHECK (end_at > start_at)
);

CREATE INDEX idx_meeting_organizer_start ON meeting (organizer_id, start_at);
CREATE INDEX idx_meeting_room_start ON meeting (room_id, start_at);
CREATE INDEX idx_meeting_run_id ON meeting (run_id);
CREATE INDEX idx_meeting_request_no ON meeting (request_no);

CREATE TABLE meeting_participant (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    employee_id BIGINT NOT NULL,
    participant_type VARCHAR(16) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_participant UNIQUE (meeting_id, employee_id),
    CONSTRAINT fk_meeting_participant_meeting
        FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_meeting_participant_employee
        FOREIGN KEY (employee_id) REFERENCES sys_user (id)
);

CREATE INDEX idx_meeting_participant_employee_id
    ON meeting_participant (employee_id);

CREATE TABLE meeting_room_slot (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    room_id BIGINT NOT NULL,
    booking_date DATE NOT NULL,
    slot_index SMALLINT NOT NULL,
    start_at DATETIME(3) NOT NULL,
    end_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_room_slot UNIQUE (room_id, booking_date, slot_index),
    CONSTRAINT fk_meeting_room_slot_meeting
        FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_meeting_room_slot_room
        FOREIGN KEY (room_id) REFERENCES meeting_room (id),
    CONSTRAINT chk_meeting_room_slot_index CHECK (slot_index BETWEEN 0 AND 47),
    CONSTRAINT chk_meeting_room_slot_time CHECK (end_at > start_at)
);

CREATE INDEX idx_meeting_room_slot_meeting_id ON meeting_room_slot (meeting_id);

CREATE TABLE employee_busy_slot (
    id BIGINT NOT NULL AUTO_INCREMENT,
    meeting_id BIGINT NOT NULL,
    employee_id BIGINT NOT NULL,
    booking_date DATE NOT NULL,
    slot_index SMALLINT NOT NULL,
    start_at DATETIME(3) NOT NULL,
    end_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_required_employee_slot
        UNIQUE (employee_id, booking_date, slot_index),
    CONSTRAINT fk_employee_busy_slot_meeting
        FOREIGN KEY (meeting_id) REFERENCES meeting (id),
    CONSTRAINT fk_employee_busy_slot_employee
        FOREIGN KEY (employee_id) REFERENCES sys_user (id),
    CONSTRAINT chk_employee_busy_slot_index CHECK (slot_index BETWEEN 0 AND 47),
    CONSTRAINT chk_employee_busy_slot_time CHECK (end_at > start_at)
);

CREATE INDEX idx_employee_busy_slot_meeting_id ON employee_busy_slot (meeting_id);

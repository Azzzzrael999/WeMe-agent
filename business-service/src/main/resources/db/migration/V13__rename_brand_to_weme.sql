-- Rename persisted demo-visible branding without deleting audit records.
UPDATE meeting
SET title = REPLACE(
    REPLACE(title, CONCAT('Meet', 'Ops'), 'WeMe'),
    CONCAT('meet', 'ops'),
    'weme'
)
WHERE LOWER(title) LIKE CONCAT('%', 'meet', 'ops', '%');

UPDATE notification
SET title = REPLACE(
        REPLACE(title, CONCAT('Meet', 'Ops'), 'WeMe'),
        CONCAT('meet', 'ops'),
        'weme'
    ),
    content = REPLACE(
        REPLACE(content, CONCAT('Meet', 'Ops'), 'WeMe'),
        CONCAT('meet', 'ops'),
        'weme'
    )
WHERE LOWER(title) LIKE CONCAT('%', 'meet', 'ops', '%')
   OR LOWER(content) LIKE CONCAT('%', 'meet', 'ops', '%');

UPDATE booking_draft
SET payload_json = REPLACE(
    REPLACE(payload_json, CONCAT('Meet', 'Ops'), 'WeMe'),
    CONCAT('meet', 'ops'),
    'weme'
)
WHERE LOWER(payload_json) LIKE CONCAT('%', 'meet', 'ops', '%');

UPDATE agent_tool_audit
SET response_json = REPLACE(
    REPLACE(response_json, CONCAT('Meet', 'Ops'), 'WeMe'),
    CONCAT('meet', 'ops'),
    'weme'
)
WHERE LOWER(response_json) LIKE CONCAT('%', 'meet', 'ops', '%');

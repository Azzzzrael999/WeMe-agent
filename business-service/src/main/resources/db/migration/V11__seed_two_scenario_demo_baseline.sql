INSERT INTO meeting (
    meeting_no, title, meeting_type, organizer_id, room_id, start_at, end_at,
    status, source, version, created_at, updated_at
)
SELECT
    'MTG-DEMO-LISI-20260826-1300',
    '支付链路发布风险评审',
    'ARCHITECTURE_REVIEW',
    1003,
    101,
    '2026-08-26 13:00:00.000',
    '2026-08-26 14:00:00.000',
    'CONFIRMED',
    'MANUAL',
    0,
    '2026-08-15 09:00:00.000',
    '2026-08-15 09:00:00.000'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT
    'MTG-DEMO-LISI-20260826-1400',
    '结算容量与回滚方案复盘',
    'GENERAL',
    1003,
    102,
    '2026-08-26 14:00:00.000',
    '2026-08-26 15:00:00.000',
    'CONFIRMED',
    'MANUAL',
    0,
    '2026-08-15 09:00:00.000',
    '2026-08-15 09:00:00.000'
WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_participant (meeting_id, employee_id, participant_type)
SELECT id, 1003, 'REQUIRED'
FROM meeting
WHERE meeting_no IN (
    'MTG-DEMO-LISI-20260826-1300',
    'MTG-DEMO-LISI-20260826-1400'
)
  AND LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_room_slot (
    meeting_id, room_id, booking_date, slot_index, start_at, end_at
)
SELECT id, room_id, '2026-08-26', 26, '2026-08-26 13:00:00.000', '2026-08-26 13:30:00.000'
FROM meeting
WHERE meeting_no = 'MTG-DEMO-LISI-20260826-1300'
  AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT id, room_id, '2026-08-26', 27, '2026-08-26 13:30:00.000', '2026-08-26 14:00:00.000'
FROM meeting
WHERE meeting_no = 'MTG-DEMO-LISI-20260826-1300'
  AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT id, room_id, '2026-08-26', 28, '2026-08-26 14:00:00.000', '2026-08-26 14:30:00.000'
FROM meeting
WHERE meeting_no = 'MTG-DEMO-LISI-20260826-1400'
  AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT id, room_id, '2026-08-26', 29, '2026-08-26 14:30:00.000', '2026-08-26 15:00:00.000'
FROM meeting
WHERE meeting_no = 'MTG-DEMO-LISI-20260826-1400'
  AND LOWER('${demo-data-enabled}') = 'true';

INSERT INTO employee_busy_slot (
    meeting_id, employee_id, booking_date, slot_index, start_at, end_at
)
SELECT id, 1003, '2026-08-26', 26, '2026-08-26 13:00:00.000', '2026-08-26 13:30:00.000'
FROM meeting
WHERE meeting_no = 'MTG-DEMO-LISI-20260826-1300'
  AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT id, 1003, '2026-08-26', 27, '2026-08-26 13:30:00.000', '2026-08-26 14:00:00.000'
FROM meeting
WHERE meeting_no = 'MTG-DEMO-LISI-20260826-1300'
  AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT id, 1003, '2026-08-26', 28, '2026-08-26 14:00:00.000', '2026-08-26 14:30:00.000'
FROM meeting
WHERE meeting_no = 'MTG-DEMO-LISI-20260826-1400'
  AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT id, 1003, '2026-08-26', 29, '2026-08-26 14:30:00.000', '2026-08-26 15:00:00.000'
FROM meeting
WHERE meeting_no = 'MTG-DEMO-LISI-20260826-1400'
  AND LOWER('${demo-data-enabled}') = 'true';

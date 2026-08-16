INSERT INTO meeting_room (
    id, code, name, building, floor, capacity, room_type, is_hot, status
)
SELECT 128, 'RD-TEAM-202', '研发楼小组讨论室', '研发楼', '2F', 4, 'HUDDLE', FALSE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true';

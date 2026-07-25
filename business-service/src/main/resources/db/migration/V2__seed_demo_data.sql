INSERT INTO department (id, name, default_building, default_floor, status)
SELECT 10, '研发中心', '研发楼', '3F', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 20, '行政中心', '总部楼', '5F', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO sys_user (
    id,
    username,
    password_hash,
    display_name,
    email,
    department_id,
    role,
    status
)
SELECT
    1001,
    'zhangsan',
    '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6',
    '张三',
    'zhangsan@example.test',
    10,
    'EMPLOYEE',
    'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT
    1002,
    'admin',
    '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6',
    '系统管理员',
    'admin@example.test',
    20,
    'ADMIN',
    'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_room (
    id,
    code,
    name,
    building,
    floor,
    capacity,
    room_type,
    is_hot,
    status
)
SELECT 101, 'RD-301', '研发楼 301', '研发楼', '3F', 8, 'STANDARD', FALSE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 102, 'RD-302', '研发楼 302', '研发楼', '3F', 16, 'STANDARD', FALSE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 103, 'HQ-VIP-501', '总部楼 VIP 501', '总部楼', '5F', 12, 'VIP', TRUE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO room_feature (id, code, name)
SELECT 1, 'WHITEBOARD', '白板'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 2, 'LARGE_SCREEN', '大屏'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 3, 'VIDEO_CONFERENCE', '视频会议'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 4, 'PROJECTOR', '投影仪'
WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_room_feature (room_id, feature_id)
SELECT 101, 1
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 101, 2
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 102, 1
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 102, 2
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 102, 4
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 103, 1
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 103, 2
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 103, 3
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 103, 4
WHERE LOWER('${demo-data-enabled}') = 'true';

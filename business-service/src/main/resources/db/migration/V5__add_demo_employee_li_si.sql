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
    1003,
    'lisi',
    '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6',
    '李四',
    'lisi@example.test',
    10,
    'EMPLOYEE',
    'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true';

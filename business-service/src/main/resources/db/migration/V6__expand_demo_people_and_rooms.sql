INSERT INTO department (id, name, default_building, default_floor, status)
SELECT 30, '产品中心', '创新楼', '6F', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 40, '销售中心', '总部楼', '8F', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 50, '财务中心', '总部楼', '7F', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 60, '人力资源中心', '协作楼', '4F', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 70, '客户成功中心', '协作楼', '6F', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 80, '法务与合规中心', '总部楼', '9F', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO sys_user (
    id, username, password_hash, display_name, email, department_id, role, status
)
SELECT 1010, 'wangwu', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '王五', 'wangwu@example.test', 10, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1011, 'zhaoliu', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '赵六', 'zhaoliu@example.test', 10, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1012, 'chenchen', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '陈晨', 'chenchen@example.test', 30, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1013, 'sunqi', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '孙琪', 'sunqi@example.test', 30, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1014, 'zhoumanager', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '周经理', 'zhoumanager@example.test', 40, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1015, 'wujing', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '吴静', 'wujing@example.test', 40, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1016, 'zhengyan', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '郑妍', 'zhengyan@example.test', 50, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1017, 'fenglei', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '冯磊', 'fenglei@example.test', 50, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1018, 'jiangmin', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '蒋敏', 'jiangmin@example.test', 60, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1019, 'shenyi', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '沈毅', 'shenyi@example.test', 70, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1020, 'hanxue', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '韩雪', 'hanxue@example.test', 70, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1021, 'qianlaw', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '钱律师', 'qianlaw@example.test', 80, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1022, 'opsadmin', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '运维管理员', 'opsadmin@example.test', 20, 'ADMIN', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 1023, 'former', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '离职员工', 'former@example.test', 10, 'EMPLOYEE', 'INACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_room (
    id, code, name, building, floor, capacity, room_type, is_hot, status
)
SELECT 110, 'RD-HUDDLE-201', '研发楼敏捷舱', '研发楼', '2F', 2, 'HUDDLE', FALSE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 111, 'RD-BOARD-401', '研发楼评审室', '研发楼', '4F', 6, 'STANDARD', FALSE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 112, 'INN-VIDEO-601', '创新楼远程协作室', '创新楼', '6F', 10, 'VIDEO', FALSE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 113, 'INN-TRAIN-701', '创新楼培训室', '创新楼', '7F', 30, 'TRAINING', TRUE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 114, 'HQ-BOARD-801', '总部楼董事会议室', '总部楼', '8F', 20, 'BOARDROOM', TRUE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 115, 'HQ-VIP-901', '总部楼贵宾厅', '总部楼', '9F', 8, 'VIP', TRUE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 116, 'COL-PHONE-301', '协作楼电话间', '协作楼', '3F', 1, 'PHONE_BOOTH', FALSE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 117, 'COL-MEET-401', '协作楼项目室', '协作楼', '4F', 12, 'STANDARD', FALSE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 118, 'COL-TOWN-601', '协作楼全员厅', '协作楼', '6F', 80, 'AUDITORIUM', TRUE, 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL
SELECT 119, 'HQ-MAINT-702', '总部楼维护中会议室', '总部楼', '7F', 14, 'STANDARD', FALSE, 'INACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_room_feature (room_id, feature_id)
SELECT 110, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 111, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 111, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 112, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 112, 3 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 113, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 113, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 113, 4 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 114, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 114, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 114, 3 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 114, 4 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 115, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 115, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 115, 3 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 116, 3 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 117, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 117, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 117, 3 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 118, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 118, 3 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 118, 4 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 119, 1 WHERE LOWER('${demo-data-enabled}') = 'true';

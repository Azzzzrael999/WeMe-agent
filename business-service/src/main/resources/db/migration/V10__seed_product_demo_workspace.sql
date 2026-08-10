INSERT INTO sys_user (
    id, username, password_hash, display_name, email, department_id, role, status
)
SELECT 1100, 'linyue', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '林悦', 'linyue@example.test', 30, 'EMPLOYEE', 'ACTIVE'
WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1101, 'gaoyuan', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '高远', 'gaoyuan@example.test', 30, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1102, 'xuchen', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '许晨', 'xuchen@example.test', 40, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1103, 'tangning', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '唐宁', 'tangning@example.test', 40, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1104, 'fanglan', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '方岚', 'fanglan@example.test', 60, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1105, 'songzhe', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '宋哲', 'songzhe@example.test', 70, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1106, 'yeqing', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '叶青', 'yeqing@example.test', 80, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1107, 'luokai', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '罗凯', 'luokai@example.test', 10, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1108, 'guran', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '顾然', 'guran@example.test', 20, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1109, 'shaowen', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '邵文', 'shaowen@example.test', 50, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1110, 'luyao', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '陆遥', 'luyao@example.test', 70, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1111, 'mengxin', '$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6', '孟欣', 'mengxin@example.test', 30, 'EMPLOYEE', 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO room_feature (id, code, name)
SELECT 5, 'WIRELESS_CAST', '无线投屏' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 6, 'MOBILE_WHITEBOARD', '移动白板' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 7, 'SIMULTANEOUS_INTERPRETATION', '同声传译' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 8, 'VISITOR_RECEPTION', '访客接待' WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_room (
    id, code, name, building, floor, capacity, room_type, is_hot, status
)
SELECT 120, 'INN-WORKSHOP-602', '创新楼共创工作坊', '创新楼', '6F', 18, 'WORKSHOP', TRUE, 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 121, 'INN-PRODUCT-603', '创新楼产品作战室', '创新楼', '6F', 12, 'STANDARD', TRUE, 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 122, 'COL-CLIENT-602', '协作楼客户共创室', '协作楼', '6F', 14, 'CLIENT', TRUE, 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 123, 'COL-INTERVIEW-402', '协作楼面试室', '协作楼', '4F', 6, 'INTERVIEW', FALSE, 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 124, 'HQ-FINANCE-703', '总部楼经营分析室', '总部楼', '7F', 10, 'BOARDROOM', FALSE, 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 125, 'HQ-LEGAL-902', '总部楼商务洽谈室', '总部楼', '9F', 8, 'STANDARD', FALSE, 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 126, 'COL-SERVICE-603', '协作楼服务复盘室', '协作楼', '6F', 10, 'VIDEO', FALSE, 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 127, 'HQ-OPS-503', '总部楼运营指挥室', '总部楼', '5F', 16, 'FOCUS', TRUE, 'ACTIVE' WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_room_feature (room_id, feature_id)
SELECT 120, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 120, 5 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 120, 6 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 121, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 121, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 121, 5 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 122, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 122, 3 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 122, 7 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 122, 8 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 123, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 124, 1 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 124, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 125, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 125, 8 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 126, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 126, 3 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 127, 2 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 127, 3 WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 127, 5 WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting (
    meeting_no, title, meeting_type, organizer_id, room_id, start_at, end_at,
    status, source, version, created_at, updated_at
)
SELECT 'MTG-PROD-RD-20260810', '新一代排期引擎方案评审', 'ARCHITECTURE_REVIEW', 1107, 120, '2026-08-10 10:00:00.000', '2026-08-10 10:30:00.000', 'COMPLETED', 'MANUAL', 0, '2026-08-07 16:20:00.000', '2026-08-10 10:30:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-PM-20260810', '三季度客户反馈专题复盘', 'PRODUCT_REVIEW', 1100, 121, '2026-08-10 14:30:00.000', '2026-08-10 15:00:00.000', 'COMPLETED', 'MANUAL', 0, '2026-08-07 17:10:00.000', '2026-08-10 15:00:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-SALES-20260811', '重点客户续约策略会', 'SALES_REVIEW', 1102, 122, '2026-08-11 09:30:00.000', '2026-08-11 10:00:00.000', 'COMPLETED', 'MANUAL', 0, '2026-08-08 11:30:00.000', '2026-08-11 10:00:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-HR-20260811', '关键岗位招聘校准会', 'INTERVIEW', 1104, 123, '2026-08-11 15:00:00.000', '2026-08-11 15:30:00.000', 'COMPLETED', 'MANUAL', 0, '2026-08-08 14:10:00.000', '2026-08-11 15:30:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-FIN-20260812', '月度经营指标复盘', 'QUARTERLY_REVIEW', 1109, 124, '2026-08-12 10:30:00.000', '2026-08-12 11:00:00.000', 'COMPLETED', 'MANUAL', 0, '2026-08-09 13:40:00.000', '2026-08-12 11:00:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-LEGAL-20260812', '战略合作协议评审', 'CONTRACT_REVIEW', 1106, 125, '2026-08-12 16:00:00.000', '2026-08-12 16:30:00.000', 'COMPLETED', 'MANUAL', 0, '2026-08-10 09:20:00.000', '2026-08-12 16:30:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-CS-20260813', '客户上线体验复盘', 'CUSTOMER_MEETING', 1105, 126, '2026-08-13 11:00:00.000', '2026-08-13 11:30:00.000', 'COMPLETED', 'MANUAL', 0, '2026-08-10 15:00:00.000', '2026-08-13 11:30:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-OPS-20260814', '下周办公运营准备会', 'TEAM_SYNC', 1108, 127, '2026-08-14 15:30:00.000', '2026-08-14 16:00:00.000', 'COMPLETED', 'MANUAL', 0, '2026-08-12 10:00:00.000', '2026-08-14 16:00:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-RD-20260817', '研发周会：发布节奏与技术风险', 'TEAM_SYNC', 1107, 120, '2026-08-17 09:30:00.000', '2026-08-17 10:00:00.000', 'CONFIRMED', 'MANUAL', 0, '2026-08-14 19:40:00.000', '2026-08-14 21:10:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-PM-20260817', '产品周评审：移动端会议体验', 'PRODUCT_REVIEW', 1100, 121, '2026-08-17 14:00:00.000', '2026-08-17 14:30:00.000', 'CONFIRMED', 'MANUAL', 0, '2026-08-14 20:00:00.000', '2026-08-14 21:20:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-SALES-20260818', '华东区客户共创方案会', 'CUSTOMER_MEETING', 1102, 122, '2026-08-18 10:00:00.000', '2026-08-18 10:30:00.000', 'CONFIRMED', 'MANUAL', 0, '2026-08-14 20:10:00.000', '2026-08-14 21:25:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-HR-20260818', '秋季校招面试官准备会', 'INTERVIEW', 1104, 123, '2026-08-18 16:00:00.000', '2026-08-18 16:30:00.000', 'CONFIRMED', 'MANUAL', 0, '2026-08-14 20:20:00.000', '2026-08-14 21:30:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-FIN-20260819', '九月预算滚动预测评审', 'QUARTERLY_REVIEW', 1109, 124, '2026-08-19 10:30:00.000', '2026-08-19 11:00:00.000', 'CONFIRMED', 'MANUAL', 0, '2026-08-14 20:30:00.000', '2026-08-14 21:35:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-LEGAL-20260819', '渠道合作合同条款评审', 'CONTRACT_REVIEW', 1106, 125, '2026-08-19 15:00:00.000', '2026-08-19 15:30:00.000', 'CONFIRMED', 'MANUAL', 0, '2026-08-14 20:40:00.000', '2026-08-14 21:40:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-CS-20260820', '客户成功服务质量复盘', 'CUSTOMER_MEETING', 1105, 126, '2026-08-20 11:00:00.000', '2026-08-20 11:30:00.000', 'CONFIRMED', 'MANUAL', 0, '2026-08-14 20:50:00.000', '2026-08-14 21:45:00.000' WHERE LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 'MTG-PROD-OPS-20260821', '办公空间运营周例会', 'TEAM_SYNC', 1108, 127, '2026-08-21 14:30:00.000', '2026-08-21 15:00:00.000', 'CONFIRMED', 'MANUAL', 0, '2026-08-14 21:00:00.000', '2026-08-14 21:50:00.000' WHERE LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_participant (meeting_id, employee_id, participant_type)
SELECT meeting.id, employee.id,
       CASE WHEN employee.id = meeting.organizer_id THEN 'REQUIRED' ELSE 'OPTIONAL' END
FROM meeting
JOIN sys_user employee
  ON employee.department_id = CASE
      WHEN meeting.meeting_no LIKE 'MTG-PROD-RD-%' THEN 10
      WHEN meeting.meeting_no LIKE 'MTG-PROD-OPS-%' THEN 20
      WHEN meeting.meeting_no LIKE 'MTG-PROD-PM-%' THEN 30
      WHEN meeting.meeting_no LIKE 'MTG-PROD-SALES-%' THEN 40
      WHEN meeting.meeting_no LIKE 'MTG-PROD-FIN-%' THEN 50
      WHEN meeting.meeting_no LIKE 'MTG-PROD-HR-%' THEN 60
      WHEN meeting.meeting_no LIKE 'MTG-PROD-CS-%' THEN 70
      WHEN meeting.meeting_no LIKE 'MTG-PROD-LEGAL-%' THEN 80
  END
WHERE meeting.meeting_no LIKE 'MTG-PROD-%'
  AND employee.status = 'ACTIVE'
  AND LOWER('${demo-data-enabled}') = 'true';

INSERT INTO meeting_room_slot (
    meeting_id, room_id, booking_date, slot_index, start_at, end_at
)
SELECT id, room_id, CAST(start_at AS DATE),
       HOUR(start_at) * 2 + CASE WHEN MINUTE(start_at) = 30 THEN 1 ELSE 0 END,
       start_at, end_at
FROM meeting
WHERE meeting_no LIKE 'MTG-PROD-%'
  AND LOWER('${demo-data-enabled}') = 'true';

INSERT INTO employee_busy_slot (
    meeting_id, employee_id, booking_date, slot_index, start_at, end_at
)
SELECT meeting.id, participant.employee_id, CAST(meeting.start_at AS DATE),
       HOUR(meeting.start_at) * 2 + CASE WHEN MINUTE(meeting.start_at) = 30 THEN 1 ELSE 0 END,
       meeting.start_at, meeting.end_at
FROM meeting
JOIN meeting_participant participant
  ON participant.meeting_id = meeting.id AND participant.participant_type = 'REQUIRED'
WHERE meeting.meeting_no LIKE 'MTG-PROD-%'
  AND LOWER('${demo-data-enabled}') = 'true';

INSERT INTO notification (user_id, type, title, content, related_meeting_id, read_at, created_at, related_replan_case_id)
SELECT employee.id, 'MEETING_CONFIRMED', '下周会议安排已更新',
       CONCAT('“', meeting.title, '”已确认，请提前查看议程与材料。'),
       meeting.id, NULL, '2026-08-14 22:50:00.000', NULL
FROM sys_user employee
JOIN meeting ON meeting.meeting_no = CASE employee.department_id
    WHEN 10 THEN 'MTG-PROD-RD-20260817'
    WHEN 20 THEN 'MTG-PROD-OPS-20260821'
    WHEN 30 THEN 'MTG-PROD-PM-20260817'
    WHEN 40 THEN 'MTG-PROD-SALES-20260818'
    WHEN 50 THEN 'MTG-PROD-FIN-20260819'
    WHEN 60 THEN 'MTG-PROD-HR-20260818'
    WHEN 70 THEN 'MTG-PROD-CS-20260820'
    WHEN 80 THEN 'MTG-PROD-LEGAL-20260819'
END
WHERE employee.status = 'ACTIVE'
  AND LOWER('${demo-data-enabled}') = 'true';

INSERT INTO notification (user_id, type, title, content, related_meeting_id, read_at, created_at, related_replan_case_id)
SELECT 1001, 'MEETING_CHANGED', '研发周会会议室已调整', '会议已调整至创新楼共创工作坊，时间保持不变。', id, NULL, '2026-08-14 23:18:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-RD-20260817' AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1001, 'PREPARATION_MISSING', '产品周评审还缺一份材料', '移动端交互原型尚未提交，补充后准备清单会自动更新。', id, NULL, '2026-08-14 23:12:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-PM-20260817' AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1001, 'MEETING_CONFIRMED', '客户共创方案会已确认', '参会范围和客户接待会议室均已确认。', id, '2026-08-14 23:10:00.000', '2026-08-14 23:08:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-SALES-20260818' AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1001, 'ACTION_ITEM_DUE_SOON', '发布检查清单将在本周到期', '请在研发周会前完成发布依赖项确认。', id, NULL, '2026-08-14 23:02:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-RD-20260817' AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1001, 'RESOURCE_RESTORED', '创新楼共创工作坊已恢复开放', '设备巡检已完成，该会议室可正常预约。', id, '2026-08-14 22:58:00.000', '2026-08-14 22:56:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-RD-20260817' AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1001, 'MEETING_CHANGED', '预算评审资料范围已更新', '本次评审新增现金流预测与采购计划两项输入。', id, NULL, '2026-08-14 22:48:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-FIN-20260819' AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1001, 'MEETING_CONFIRMED', '合同条款评审已确认', '法务、销售与客户成功团队的参会安排已同步。', id, '2026-08-14 22:45:00.000', '2026-08-14 22:40:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-LEGAL-20260819' AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1001, 'ACTION_ITEM_OVERDUE', '客户体验复盘跟进项待处理', '请补充问题负责人和预计完成时间。', id, NULL, '2026-08-14 22:35:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-CS-20260813' AND LOWER('${demo-data-enabled}') = 'true';

INSERT INTO notification (user_id, type, title, content, related_meeting_id, read_at, created_at, related_replan_case_id)
SELECT 1002, 'MEETING_CHANGED', '下周重点会议安排已汇总', '八个业务团队的周计划已同步，可从会议日历查看。', id, NULL, '2026-08-14 23:16:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-OPS-20260821' AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1002, 'PREPARATION_MISSING', '产品周评审存在缺失材料', '移动端交互原型仍待产品团队补充。', id, NULL, '2026-08-14 23:05:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-PM-20260817' AND LOWER('${demo-data-enabled}') = 'true'
UNION ALL SELECT 1002, 'RESOURCE_RESTORED', '创新楼共创工作坊恢复开放', '设施巡检已完成，资源状态已恢复为可用。', id, '2026-08-14 23:01:00.000', '2026-08-14 23:00:00.000', NULL FROM meeting WHERE meeting_no = 'MTG-PROD-RD-20260817' AND LOWER('${demo-data-enabled}') = 'true';

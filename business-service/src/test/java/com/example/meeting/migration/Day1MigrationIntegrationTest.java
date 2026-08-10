package com.example.meeting.migration;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

@SpringBootTest
@ActiveProfiles("test")
class Day1MigrationIntegrationTest {

  private static final List<String> DAY_ONE_TABLES =
      List.of(
          "department",
          "sys_user",
          "meeting_room",
          "room_feature",
          "meeting_room_feature",
          "meeting",
          "meeting_participant",
          "meeting_room_slot",
          "employee_busy_slot");

  @Autowired private JdbcTemplate jdbcTemplate;

  @Test
  void flywayCreatesDayOneThroughDayThreeJavaTablesOnly() {
    for (String table : DAY_ONE_TABLES) {
      assertThat(tableCount(table)).as("table %s", table).isEqualTo(1);
    }
    assertThat(tableCount("booking_draft")).isEqualTo(1);
    assertThat(tableCount("booking_request")).isEqualTo(1);
    assertThat(tableCount("idempotency_record")).isEqualTo(1);
    assertThat(tableCount("message_outbox")).isEqualTo(1);
    assertThat(tableCount("event_consume_record")).isEqualTo(1);
    assertThat(tableCount("notification")).isEqualTo(1);
    assertThat(tableCount("meeting_replan_case")).isEqualTo(1);
    assertThat(tableCount("agent_tool_audit")).isEqualTo(1);
    assertThat(tableCount("meeting_lifecycle_profile")).isEqualTo(1);
    assertThat(tableCount("meeting_agenda_item")).isEqualTo(1);
    assertThat(tableCount("meeting_material")).isEqualTo(1);
    assertThat(tableCount("meeting_reminder_delivery")).isEqualTo(1);
    assertThat(tableCount("post_meeting_draft")).isEqualTo(1);
    assertThat(tableCount("meeting_minutes")).isEqualTo(1);
    assertThat(tableCount("meeting_decision")).isEqualTo(1);
    assertThat(tableCount("meeting_action_item")).isEqualTo(1);
    assertThat(tableCount("action_item_reminder_delivery")).isEqualTo(1);
    assertThat(tableCount("agent_run")).isZero();
  }

  @Test
  void demoSeedContainsVariedPeopleDepartmentsRoomsAndFeatureTypes() {
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM sys_user WHERE id BETWEEN 1001 AND 1111", Integer.class))
        .isEqualTo(29);
    assertThat(jdbcTemplate.queryForList("SELECT role FROM sys_user ORDER BY role", String.class))
        .contains("ADMIN", "EMPLOYEE");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(DISTINCT department_id) FROM sys_user WHERE status = 'ACTIVE'",
                Integer.class))
        .isEqualTo(8);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM sys_user WHERE role = 'ADMIN' AND status = 'ACTIVE'",
                Integer.class))
        .isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM sys_user WHERE id = 1023 AND username = 'former' AND status = 'DISABLED'",
                Integer.class))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM sys_user WHERE status = 'INACTIVE'", Integer.class))
        .isZero();
    assertThat(
            jdbcTemplate.queryForList(
                "SELECT id, username, display_name, department_id FROM sys_user WHERE id IN (1001,1003) ORDER BY id"))
        .containsExactly(
            java.util.Map.of(
                "id", 1001L, "username", "zhangsan", "display_name", "张三", "department_id", 10L),
            java.util.Map.of(
                "id", 1003L, "username", "lisi", "display_name", "李四", "department_id", 10L));
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting_room", Integer.class))
        .isEqualTo(21);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(DISTINCT room_type) FROM meeting_room", Integer.class))
        .isEqualTo(12);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(DISTINCT building) FROM meeting_room", Integer.class))
        .isEqualTo(4);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room WHERE is_hot = TRUE", Integer.class))
        .isEqualTo(9);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room WHERE code = 'HQ-MAINT-702' AND room_type = 'STANDARD'",
                Integer.class))
        .isEqualTo(1);
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM room_feature", Integer.class))
        .isEqualTo(8);
  }

  @Test
  void lifecycleLongTextColumnsCanStoreWorstCaseUtf8ContractLengths() {
    assertThat(characterCapacity("post_meeting_draft", "transcript")).isGreaterThan(65535L);
    assertThat(characterCapacity("post_meeting_draft", "payload_json")).isGreaterThan(65535L);
    assertThat(characterCapacity("meeting_minutes", "discussion_summary")).isGreaterThan(65535L);
  }

  private long characterCapacity(String tableName, String columnName) {
    Long capacity =
        jdbcTemplate.queryForObject(
            """
                        SELECT character_maximum_length
                        FROM information_schema.columns
                        WHERE LOWER(table_name) = ? AND LOWER(column_name) = ?
                        """,
            Long.class,
            tableName,
            columnName);
    return capacity == null ? 0 : capacity;
  }

  private int tableCount(String tableName) {
    Integer count =
        jdbcTemplate.queryForObject(
            """
                        SELECT COUNT(*)
                        FROM information_schema.tables
                        WHERE LOWER(table_name) = ?
                        """,
            Integer.class,
            tableName);
    return count == null ? 0 : count;
  }
}

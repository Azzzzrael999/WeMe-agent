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
    assertThat(tableCount("agent_tool_audit")).isEqualTo(1);
    assertThat(tableCount("agent_run")).isZero();
    assertThat(tableCount("video_conference_link")).isZero();
  }

  @Test
  void demoSeedContainsTwoRolesThreeRoomsAndFourFeatureTypes() {
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM sys_user", Integer.class))
        .isEqualTo(2);
    assertThat(jdbcTemplate.queryForList("SELECT role FROM sys_user ORDER BY role", String.class))
        .containsExactly("ADMIN", "EMPLOYEE");
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting_room", Integer.class))
        .isEqualTo(3);
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM room_feature", Integer.class))
        .isEqualTo(4);
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

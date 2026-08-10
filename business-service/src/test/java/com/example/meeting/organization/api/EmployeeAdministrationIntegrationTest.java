package com.example.meeting.organization.api;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class EmployeeAdministrationIntegrationTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;

  @Test
  void adminCanManageEmployeeLifecycleAndResetPassword() throws Exception {
    String adminToken = login("admin", "demo-password", 200);
    String suffix = UUID.randomUUID().toString().replace("-", "").substring(0, 8);
    String username = "test_" + suffix;
    String email = username + "@example.test";
    String initialPassword = "temporary-password";
    String newPassword = "changed-password";

    MvcResult created =
        mockMvc
            .perform(
                post("/api/v1/admin/employees")
                    .header("Authorization", bearer(adminToken))
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(
                        """
                        {
                          "username":"%s",
                          "initialPassword":"%s",
                          "displayName":"测试员工",
                          "email":"%s",
                          "departmentId":10,
                          "role":"EMPLOYEE",
                          "status":"ACTIVE"
                        }
                        """
                            .formatted(username.toUpperCase(), initialPassword, email)))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.username").value(username))
            .andExpect(jsonPath("$.data.version").value(0))
            .andExpect(jsonPath("$.data.passwordHash").doesNotExist())
            .andReturn();
    long employeeId = data(created).get("id").longValue();

    mockMvc
        .perform(
            get("/api/v1/admin/employees")
                .header("Authorization", bearer(adminToken))
                .param("keyword", suffix)
                .param("departmentId", "10")
                .param("role", "EMPLOYEE")
                .param("status", "ACTIVE"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(1))
        .andExpect(jsonPath("$.data.items[0].id").value(employeeId));

    mockMvc
        .perform(
            put("/api/v1/admin/employees/{id}", employeeId)
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "displayName":"测试员工（更新）",
                      "email":"%s",
                      "departmentId":20,
                      "role":"EMPLOYEE",
                      "expectedVersion":0
                    }
                    """
                        .formatted(email)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.departmentId").value(20))
        .andExpect(jsonPath("$.data.version").value(1));

    mockMvc
        .perform(
            post("/api/v1/admin/employees/{id}/password", employeeId)
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {"newPassword":"%s","expectedVersion":1}
                    """
                        .formatted(newPassword)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.version").value(2))
        .andExpect(jsonPath("$.data.newPassword").doesNotExist());

    login(username, initialPassword, 401);
    String employeeToken = login(username, newPassword, 200);

    mockMvc
        .perform(
            patch("/api/v1/admin/employees/{id}/status", employeeId)
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"DISABLED\",\"expectedVersion\":2}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.version").value(3));

    mockMvc
        .perform(get("/api/v1/auth/me").header("Authorization", bearer(employeeToken)))
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.code").value("AUTH_REQUIRED"));

    mockMvc
        .perform(
            patch("/api/v1/admin/employees/{id}/status", employeeId)
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"ACTIVE\",\"expectedVersion\":3}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.version").value(4));
  }

  @Test
  void employeeRbacAndStableManagementConflictsAreEnforced() throws Exception {
    String employeeToken = login("zhangsan", "demo-password", 200);
    String adminToken = login("admin", "demo-password", 200);

    mockMvc
        .perform(get("/api/v1/admin/departments").header("Authorization", bearer(employeeToken)))
        .andExpect(status().isForbidden())
        .andExpect(jsonPath("$.code").value("FORBIDDEN"));

    mockMvc
        .perform(
            post("/api/v1/admin/employees")
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "username":"zhangsan",
                      "initialPassword":"temporary-password",
                      "displayName":"重复用户名",
                      "email":"unique-conflict@example.test",
                      "departmentId":10,
                      "role":"EMPLOYEE",
                      "status":"ACTIVE"
                    }
                    """))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("EMPLOYEE_USERNAME_CONFLICT"));

    mockMvc
        .perform(
            post("/api/v1/admin/employees")
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "username":"unique_email_conflict",
                      "initialPassword":"temporary-password",
                      "displayName":"重复邮箱",
                      "email":"admin@example.test",
                      "departmentId":10,
                      "role":"EMPLOYEE",
                      "status":"ACTIVE"
                    }
                    """))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("EMPLOYEE_EMAIL_CONFLICT"));

    mockMvc
        .perform(
            post("/api/v1/admin/employees")
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "username":"unknown_department_user",
                      "initialPassword":"temporary-password",
                      "displayName":"未知部门",
                      "email":"unknown-department@example.test",
                      "departmentId":999999,
                      "role":"EMPLOYEE",
                      "status":"ACTIVE"
                    }
                    """))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("DEPARTMENT_NOT_FOUND"));

    mockMvc
        .perform(
            put("/api/v1/admin/employees/1001")
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "displayName":"张三",
                      "email":"zhangsan@example.test",
                      "departmentId":10,
                      "role":"EMPLOYEE",
                      "expectedVersion":999
                    }
                    """))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("EMPLOYEE_STATE_CONFLICT"));

    MvcResult adminDetails =
        mockMvc
            .perform(
                get("/api/v1/admin/employees/1002").header("Authorization", bearer(adminToken)))
            .andExpect(status().isOk())
            .andReturn();
    int adminVersion = data(adminDetails).get("version").intValue();

    mockMvc
        .perform(
            patch("/api/v1/admin/employees/1002/status")
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"DISABLED\",\"expectedVersion\":" + adminVersion + "}"))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("EMPLOYEE_STATE_CONFLICT"));

    mockMvc
        .perform(
            put("/api/v1/admin/employees/1002")
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "displayName":"系统管理员",
                      "email":"admin@example.test",
                      "departmentId":20,
                      "role":"EMPLOYEE",
                      "expectedVersion":%d
                    }
                    """
                        .formatted(adminVersion)))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("EMPLOYEE_STATE_CONFLICT"));
  }

  private String login(String username, String password, int expectedStatus) throws Exception {
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/auth/login")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(
                        """
                        {"username":"%s","password":"%s"}
                        """
                            .formatted(username, password)))
            .andExpect(status().is(expectedStatus))
            .andReturn();
    if (expectedStatus != 200) {
      return null;
    }
    return data(result).get("accessToken").asText();
  }

  private JsonNode data(MvcResult result) throws Exception {
    return objectMapper.readTree(result.getResponse().getContentAsString()).get("data");
  }

  private String bearer(String token) {
    return "Bearer " + token;
  }
}

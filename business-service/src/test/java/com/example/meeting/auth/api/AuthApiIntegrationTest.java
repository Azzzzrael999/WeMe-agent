package com.example.meeting.auth.api;

import static org.hamcrest.Matchers.endsWith;
import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AuthApiIntegrationTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;

  @Test
  void employeeCanLoginAndReadCurrentUser() throws Exception {
    String traceId = "trc_auth_integration";
    String responseBody =
        mockMvc
            .perform(
                post("/api/v1/auth/login")
                    .header("X-Trace-Id", traceId)
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(
                        """
                                                {
                                                  "username": "zhangsan",
                                                  "password": "demo-password"
                                                }
                                                """))
            .andExpect(status().isOk())
            .andExpect(header().string("X-Trace-Id", traceId))
            .andExpect(jsonPath("$.traceId").value(traceId))
            .andExpect(jsonPath("$.timestamp").value(endsWith("+08:00")))
            .andExpect(jsonPath("$.data.tokenType").value("Bearer"))
            .andExpect(jsonPath("$.data.expiresIn").value(7200))
            .andExpect(jsonPath("$.data.accessToken").isNotEmpty())
            .andExpect(jsonPath("$.data.user.id").value(1001))
            .andExpect(jsonPath("$.data.user.username").value("zhangsan"))
            .andExpect(jsonPath("$.data.user.displayName").value("张三"))
            .andExpect(jsonPath("$.data.user.email").value("zhangsan@example.test"))
            .andExpect(jsonPath("$.data.user.departmentId").value(10))
            .andExpect(jsonPath("$.data.user.departmentName").value("研发中心"))
            .andExpect(jsonPath("$.data.user.roles", hasSize(1)))
            .andExpect(jsonPath("$.data.user.roles[0]").value("EMPLOYEE"))
            .andReturn()
            .getResponse()
            .getContentAsString();

    JsonNode loginEnvelope = objectMapper.readTree(responseBody);
    String accessToken = loginEnvelope.at("/data/accessToken").asText();

    mockMvc
        .perform(
            get("/api/v1/auth/me")
                .header("Authorization", "Bearer " + accessToken)
                .header("X-Trace-Id", "trc_me_integration"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.traceId").value("trc_me_integration"))
        .andExpect(jsonPath("$.data.id").value(1001))
        .andExpect(jsonPath("$.data.username").value("zhangsan"))
        .andExpect(jsonPath("$.data.roles[0]").value("EMPLOYEE"));
  }

  @Test
  void adminCanLoginWithAdminRole() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                        {
                                          "username": "admin",
                                          "password": "demo-password"
                                        }
                                        """))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.user.username").value("admin"))
        .andExpect(jsonPath("$.data.user.roles[0]").value("ADMIN"));
  }

  @Test
  void invalidCredentialsUseStableAuthErrorEnvelope() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/auth/login")
                .header("X-Trace-Id", "trc_bad_credentials")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                        {
                                          "username": "zhangsan",
                                          "password": "wrong-password"
                                        }
                                        """))
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.code").value("AUTH_REQUIRED"))
        .andExpect(jsonPath("$.message").value("用户名或密码错误"))
        .andExpect(jsonPath("$.details", hasSize(0)))
        .andExpect(jsonPath("$.traceId").value("trc_bad_credentials"))
        .andExpect(jsonPath("$.timestamp").doesNotExist());
  }

  @Test
  void invalidLoginBodyUsesValidationErrorEnvelope() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"username\":\"\",\"password\":\"\"}"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details", hasSize(2)))
        .andExpect(jsonPath("$.traceId").isNotEmpty());
  }

  @Test
  void currentUserRequiresBearerToken() throws Exception {
    mockMvc
        .perform(get("/api/v1/auth/me").header("X-Trace-Id", "trc_no_token"))
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.code").value("AUTH_REQUIRED"))
        .andExpect(jsonPath("$.details", hasSize(0)))
        .andExpect(jsonPath("$.traceId").value("trc_no_token"));
  }
}

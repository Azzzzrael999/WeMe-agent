package com.example.meeting.room.api;

import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
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
class RoomApiIntegrationTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;

  @Test
  void authenticatedEmployeeCanListSeededRoomsAndFeatures() throws Exception {
    String accessToken = loginAs("zhangsan");

    mockMvc
        .perform(
            get("/api/v1/rooms")
                .header("Authorization", "Bearer " + accessToken)
                .header("X-Trace-Id", "trc_rooms"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.traceId").value("trc_rooms"))
        .andExpect(jsonPath("$.data.total").value(3))
        .andExpect(jsonPath("$.data.items", hasSize(3)))
        .andExpect(jsonPath("$.data.items[0].id").value(103))
        .andExpect(jsonPath("$.data.items[0].code").value("HQ-VIP-501"))
        .andExpect(jsonPath("$.data.items[0].roomType").value("VIP"))
        .andExpect(jsonPath("$.data.items[0].isHot").value(true))
        .andExpect(jsonPath("$.data.items[0].status").value("ACTIVE"))
        .andExpect(jsonPath("$.data.items[0].features", hasSize(4)))
        .andExpect(jsonPath("$.data.items[1].id").value(101))
        .andExpect(jsonPath("$.data.items[1].isHot").value(false))
        .andExpect(jsonPath("$.data.items[1].features", hasSize(2)))
        .andExpect(jsonPath("$.data.items[2].id").value(102))
        .andExpect(jsonPath("$.data.items[2].features", hasSize(3)));
  }

  @Test
  void roomListRejectsMissingOrTamperedToken() throws Exception {
    mockMvc
        .perform(get("/api/v1/rooms"))
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.code").value("AUTH_REQUIRED"));

    mockMvc
        .perform(get("/api/v1/rooms").header("Authorization", "Bearer invalid.jwt.value"))
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.code").value("AUTH_REQUIRED"));
  }

  private String loginAs(String username) throws Exception {
    String responseBody =
        mockMvc
            .perform(
                post("/api/v1/auth/login")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(
                        """
                                                {"username":"%s","password":"demo-password"}
                                                """
                            .formatted(username)))
            .andExpect(status().isOk())
            .andReturn()
            .getResponse()
            .getContentAsString();
    JsonNode envelope = objectMapper.readTree(responseBody);
    return envelope.at("/data/accessToken").asText();
  }
}

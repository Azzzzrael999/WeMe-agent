package com.example.meeting.knowledge.api;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.example.meeting.knowledge.application.KnowledgeDocumentGateway;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class KnowledgeDocumentApiIntegrationTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @MockBean private KnowledgeDocumentGateway gateway;

  @Test
  void employeeCanBrowseButCannotManageKnowledgeDocuments() throws Exception {
    String employeeToken = login("zhangsan", "demo-password");
    KnowledgeDocumentView document = document("制度正文");
    when(gateway.list(isNull(), isNull(), anyInt(), anyInt(), any(), anyString()))
        .thenReturn(new KnowledgeDocumentListView(List.of(document), 1));
    when(gateway.get(anyString(), any(), anyString())).thenReturn(document);

    mockMvc
        .perform(get("/api/v1/knowledge-documents").header("Authorization", bearer(employeeToken)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(1))
        .andExpect(jsonPath("$.data.items[0].documentId").value("doc_managed_policy"))
        .andExpect(jsonPath("$.data.items[0].content").value("制度正文"));

    mockMvc
        .perform(
            delete("/api/v1/admin/knowledge-documents/doc_managed_policy")
                .param("expectedVersion", "0")
                .header("Authorization", bearer(employeeToken)))
        .andExpect(status().isForbidden())
        .andExpect(jsonPath("$.code").value("FORBIDDEN"));
  }

  @Test
  void adminCanUploadEditAndDeleteThroughPublicApi() throws Exception {
    String adminToken = login("admin", "demo-password");
    KnowledgeDocumentView document = document("制度正文");
    when(gateway.upload(any(), isNull(), any(), anyString())).thenReturn(document);
    when(gateway.update(anyString(), any(), any(), anyString())).thenReturn(document);
    when(gateway.delete(anyString(), anyInt(), any(), anyString()))
        .thenReturn(new KnowledgeDocumentGateway.DeleteResult("doc_managed_policy", "DELETED", 1));
    MockMultipartFile file =
        new MockMultipartFile("file", "managed.md", "text/markdown", "# 制度正文".getBytes());

    mockMvc
        .perform(
            multipart("/api/v1/admin/knowledge-documents")
                .file(file)
                .header("Authorization", bearer(adminToken)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("INDEXED"));

    mockMvc
        .perform(
            put("/api/v1/admin/knowledge-documents/doc_managed_policy")
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"content\":\"# 更新正文\",\"expectedVersion\":0}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.editable").value(true));

    mockMvc
        .perform(
            delete("/api/v1/admin/knowledge-documents/doc_managed_policy")
                .param("expectedVersion", "0")
                .header("Authorization", bearer(adminToken)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("DELETED"));
  }

  private KnowledgeDocumentView document(String content) {
    return new KnowledgeDocumentView(
        "doc_managed_policy",
        "在线维护制度",
        "MEETING_POLICY",
        "ALL",
        "1.0",
        "2026-08-01",
        180,
        "managed.md",
        "text/markdown",
        "INDEXED",
        2,
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        0,
        "2026-08-15T10:00:00+08:00",
        "2026-08-15T10:00:00+08:00",
        "2026-08-15T10:00:00+08:00",
        true,
        content);
  }

  private String login(String username, String password) throws Exception {
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
            .andExpect(status().isOk())
            .andReturn();
    JsonNode data = objectMapper.readTree(result.getResponse().getContentAsString()).get("data");
    return data.get("accessToken").asText();
  }

  private String bearer(String token) {
    return "Bearer " + token;
  }
}

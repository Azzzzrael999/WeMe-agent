package com.example.meeting.knowledge.api;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record UpdateKnowledgeDocumentRequest(
    @NotBlank @Size(max = 500000) String content,
    @Min(0) @Max(Integer.MAX_VALUE) int expectedVersion) {}

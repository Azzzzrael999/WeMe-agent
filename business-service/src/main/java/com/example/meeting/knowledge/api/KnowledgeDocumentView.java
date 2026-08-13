package com.example.meeting.knowledge.api;

public record KnowledgeDocumentView(
    String documentId,
    String title,
    String documentType,
    String department,
    String version,
    String effectiveDate,
    int priority,
    String fileName,
    String mediaType,
    String status,
    int chunkCount,
    String checksum,
    int recordVersion,
    String createdAt,
    String updatedAt,
    String indexedAt,
    boolean editable,
    String content) {}

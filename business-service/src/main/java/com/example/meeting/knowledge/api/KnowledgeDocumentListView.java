package com.example.meeting.knowledge.api;

import java.util.List;

public record KnowledgeDocumentListView(List<KnowledgeDocumentView> items, long total) {

  public KnowledgeDocumentListView {
    items = List.copyOf(items);
  }
}

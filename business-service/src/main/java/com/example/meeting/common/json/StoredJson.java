package com.example.meeting.common.json;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

public final class StoredJson {

  private StoredJson() {}

  public static <T> T read(ObjectMapper objectMapper, String value, Class<T> type)
      throws JsonProcessingException {
    JsonNode node = objectMapper.readTree(value);
    for (int depth = 0; depth < 2 && node.isTextual(); depth++) {
      node = objectMapper.readTree(node.textValue());
    }
    return objectMapper.treeToValue(node, type);
  }
}

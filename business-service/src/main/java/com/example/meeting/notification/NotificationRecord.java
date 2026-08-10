package com.example.meeting.notification;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import java.time.LocalDateTime;

@TableName("notification")
public class NotificationRecord {
  @TableId private Long id;
  private Long userId;
  private String type;
  private String title;
  private String content;
  private Long relatedMeetingId;
  private Long relatedReplanCaseId;
  private LocalDateTime readAt;
  private LocalDateTime createdAt;

  public Long getId() {
    return id;
  }

  public void setId(Long id) {
    this.id = id;
  }

  public Long getUserId() {
    return userId;
  }

  public void setUserId(Long userId) {
    this.userId = userId;
  }

  public String getType() {
    return type;
  }

  public void setType(String type) {
    this.type = type;
  }

  public String getTitle() {
    return title;
  }

  public void setTitle(String title) {
    this.title = title;
  }

  public String getContent() {
    return content;
  }

  public void setContent(String content) {
    this.content = content;
  }

  public Long getRelatedMeetingId() {
    return relatedMeetingId;
  }

  public void setRelatedMeetingId(Long relatedMeetingId) {
    this.relatedMeetingId = relatedMeetingId;
  }

  public Long getRelatedReplanCaseId() {
    return relatedReplanCaseId;
  }

  public void setRelatedReplanCaseId(Long relatedReplanCaseId) {
    this.relatedReplanCaseId = relatedReplanCaseId;
  }

  public LocalDateTime getReadAt() {
    return readAt;
  }

  public void setReadAt(LocalDateTime readAt) {
    this.readAt = readAt;
  }

  public LocalDateTime getCreatedAt() {
    return createdAt;
  }

  public void setCreatedAt(LocalDateTime createdAt) {
    this.createdAt = createdAt;
  }
}

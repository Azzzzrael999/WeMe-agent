package com.example.meeting.replan.domain;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import java.time.LocalDateTime;

@TableName("meeting_replan_case")
public class ReplanCaseRecord {

  @TableId private Long id;
  private String caseNo;
  private Long meetingId;
  private Long organizerId;
  private Long failedRoomId;
  private String failedRoomName;
  private String failureReason;
  private Integer roomStatusVersion;
  private LocalDateTime originalStartAt;
  private LocalDateTime originalEndAt;
  private String constraintSnapshot;
  private String status;
  private String resolutionType;
  private Long resolvedRoomId;
  private LocalDateTime resolvedStartAt;
  private LocalDateTime resolvedEndAt;
  private Integer version;
  private LocalDateTime createdAt;
  private LocalDateTime updatedAt;
  private LocalDateTime resolvedAt;

  public Long getId() {
    return id;
  }

  public void setId(Long id) {
    this.id = id;
  }

  public String getCaseNo() {
    return caseNo;
  }

  public void setCaseNo(String caseNo) {
    this.caseNo = caseNo;
  }

  public Long getMeetingId() {
    return meetingId;
  }

  public void setMeetingId(Long meetingId) {
    this.meetingId = meetingId;
  }

  public Long getOrganizerId() {
    return organizerId;
  }

  public void setOrganizerId(Long organizerId) {
    this.organizerId = organizerId;
  }

  public Long getFailedRoomId() {
    return failedRoomId;
  }

  public void setFailedRoomId(Long failedRoomId) {
    this.failedRoomId = failedRoomId;
  }

  public String getFailedRoomName() {
    return failedRoomName;
  }

  public void setFailedRoomName(String failedRoomName) {
    this.failedRoomName = failedRoomName;
  }

  public String getFailureReason() {
    return failureReason;
  }

  public void setFailureReason(String failureReason) {
    this.failureReason = failureReason;
  }

  public Integer getRoomStatusVersion() {
    return roomStatusVersion;
  }

  public void setRoomStatusVersion(Integer roomStatusVersion) {
    this.roomStatusVersion = roomStatusVersion;
  }

  public LocalDateTime getOriginalStartAt() {
    return originalStartAt;
  }

  public void setOriginalStartAt(LocalDateTime originalStartAt) {
    this.originalStartAt = originalStartAt;
  }

  public LocalDateTime getOriginalEndAt() {
    return originalEndAt;
  }

  public void setOriginalEndAt(LocalDateTime originalEndAt) {
    this.originalEndAt = originalEndAt;
  }

  public String getConstraintSnapshot() {
    return constraintSnapshot;
  }

  public void setConstraintSnapshot(String constraintSnapshot) {
    this.constraintSnapshot = constraintSnapshot;
  }

  public String getStatus() {
    return status;
  }

  public void setStatus(String status) {
    this.status = status;
  }

  public String getResolutionType() {
    return resolutionType;
  }

  public void setResolutionType(String resolutionType) {
    this.resolutionType = resolutionType;
  }

  public Long getResolvedRoomId() {
    return resolvedRoomId;
  }

  public void setResolvedRoomId(Long resolvedRoomId) {
    this.resolvedRoomId = resolvedRoomId;
  }

  public LocalDateTime getResolvedStartAt() {
    return resolvedStartAt;
  }

  public void setResolvedStartAt(LocalDateTime resolvedStartAt) {
    this.resolvedStartAt = resolvedStartAt;
  }

  public LocalDateTime getResolvedEndAt() {
    return resolvedEndAt;
  }

  public void setResolvedEndAt(LocalDateTime resolvedEndAt) {
    this.resolvedEndAt = resolvedEndAt;
  }

  public Integer getVersion() {
    return version;
  }

  public void setVersion(Integer version) {
    this.version = version;
  }

  public LocalDateTime getCreatedAt() {
    return createdAt;
  }

  public void setCreatedAt(LocalDateTime createdAt) {
    this.createdAt = createdAt;
  }

  public LocalDateTime getUpdatedAt() {
    return updatedAt;
  }

  public void setUpdatedAt(LocalDateTime updatedAt) {
    this.updatedAt = updatedAt;
  }

  public LocalDateTime getResolvedAt() {
    return resolvedAt;
  }

  public void setResolvedAt(LocalDateTime resolvedAt) {
    this.resolvedAt = resolvedAt;
  }
}

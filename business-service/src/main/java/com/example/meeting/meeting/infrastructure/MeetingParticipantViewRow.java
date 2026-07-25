package com.example.meeting.meeting.infrastructure;

public class MeetingParticipantViewRow {

  private Long employeeId;
  private String displayName;
  private String participantType;

  public Long getEmployeeId() {
    return employeeId;
  }

  public void setEmployeeId(Long employeeId) {
    this.employeeId = employeeId;
  }

  public String getDisplayName() {
    return displayName;
  }

  public void setDisplayName(String displayName) {
    this.displayName = displayName;
  }

  public String getParticipantType() {
    return participantType;
  }

  public void setParticipantType(String participantType) {
    this.participantType = participantType;
  }
}

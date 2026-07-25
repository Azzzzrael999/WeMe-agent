package com.example.meeting.organization.domain;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

@TableName("department")
public class Department {

  @TableId private Long id;
  private String name;
  private String defaultBuilding;
  private String defaultFloor;
  private String status;

  public Long getId() {
    return id;
  }

  public void setId(Long id) {
    this.id = id;
  }

  public String getName() {
    return name;
  }

  public void setName(String name) {
    this.name = name;
  }

  public String getDefaultBuilding() {
    return defaultBuilding;
  }

  public void setDefaultBuilding(String defaultBuilding) {
    this.defaultBuilding = defaultBuilding;
  }

  public String getDefaultFloor() {
    return defaultFloor;
  }

  public void setDefaultFloor(String defaultFloor) {
    this.defaultFloor = defaultFloor;
  }

  public String getStatus() {
    return status;
  }

  public void setStatus(String status) {
    this.status = status;
  }
}

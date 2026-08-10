package com.example.meeting.organization.application;

import com.example.meeting.organization.api.EmployeeDirectoryItemView;
import com.example.meeting.organization.infrastructure.EmployeeAdminMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class EmployeeDirectoryService {

  private final EmployeeAdminMapper employeeMapper;

  public EmployeeDirectoryService(EmployeeAdminMapper employeeMapper) {
    this.employeeMapper = employeeMapper;
  }

  @Transactional(readOnly = true)
  public java.util.List<EmployeeDirectoryItemView> employees() {
    return employeeMapper.findDirectoryEmployees().stream()
        .map(
            employee ->
                new EmployeeDirectoryItemView(
                    employee.getId(),
                    employee.getDisplayName(),
                    employee.getDepartmentId(),
                    employee.getDepartmentName()))
        .toList();
  }
}

package com.example.meeting.organization.api;

import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.organization.application.EmployeeAdministrationService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/admin")
public class AdminEmployeeController {

  private final EmployeeAdministrationService service;
  private final ApiResponseFactory responseFactory;

  public AdminEmployeeController(
      EmployeeAdministrationService service, ApiResponseFactory responseFactory) {
    this.service = service;
    this.responseFactory = responseFactory;
  }

  @GetMapping("/departments")
  public ApiSuccess<DepartmentListView> departments(HttpServletRequest request) {
    return responseFactory.success(new DepartmentListView(service.departments()), request);
  }

  @GetMapping("/employees")
  public ApiSuccess<EmployeeListView> list(
      @RequestParam(required = false) String keyword,
      @RequestParam(required = false) Long departmentId,
      @RequestParam(required = false) String role,
      @RequestParam(required = false) String status,
      @RequestParam(defaultValue = "1") int page,
      @RequestParam(defaultValue = "20") int size,
      HttpServletRequest request) {
    return responseFactory.success(
        service.list(keyword, departmentId, role, status, page, size), request);
  }

  @GetMapping("/employees/{employeeId}")
  public ApiSuccess<EmployeeItemView> get(
      @PathVariable long employeeId, HttpServletRequest request) {
    return responseFactory.success(service.get(employeeId), request);
  }

  @PostMapping("/employees")
  public ApiSuccess<EmployeeItemView> create(
      @Valid @RequestBody CreateEmployeeRequest body, HttpServletRequest request) {
    return responseFactory.success(service.create(body), request);
  }

  @PutMapping("/employees/{employeeId}")
  public ApiSuccess<EmployeeItemView> update(
      @PathVariable long employeeId,
      @Valid @RequestBody UpdateEmployeeRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(service.update(employeeId, body, actor), request);
  }

  @PatchMapping("/employees/{employeeId}/status")
  public ApiSuccess<EmployeeItemView> updateStatus(
      @PathVariable long employeeId,
      @Valid @RequestBody UpdateEmployeeStatusRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(service.updateStatus(employeeId, body, actor), request);
  }

  @PostMapping("/employees/{employeeId}/password")
  public ApiSuccess<EmployeeItemView> resetPassword(
      @PathVariable long employeeId,
      @Valid @RequestBody ResetEmployeePasswordRequest body,
      HttpServletRequest request) {
    return responseFactory.success(service.resetPassword(employeeId, body), request);
  }

  public record DepartmentListView(List<DepartmentOptionView> items) {
    public DepartmentListView {
      items = List.copyOf(items);
    }
  }
}

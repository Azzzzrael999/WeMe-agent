package com.example.meeting.organization.api;

import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.organization.application.EmployeeDirectoryService;
import jakarta.servlet.http.HttpServletRequest;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/directory")
public class EmployeeDirectoryController {

  private final EmployeeDirectoryService service;
  private final ApiResponseFactory responseFactory;

  public EmployeeDirectoryController(
      EmployeeDirectoryService service, ApiResponseFactory responseFactory) {
    this.service = service;
    this.responseFactory = responseFactory;
  }

  @GetMapping("/employees")
  public ApiSuccess<EmployeeDirectoryView> employees(HttpServletRequest request) {
    return responseFactory.success(new EmployeeDirectoryView(service.employees()), request);
  }

  public record EmployeeDirectoryView(List<EmployeeDirectoryItemView> items) {
    public EmployeeDirectoryView {
      items = List.copyOf(items);
    }
  }
}

package com.example.meeting.organization.application;

import com.example.meeting.auth.domain.UserAccount;
import com.example.meeting.auth.infrastructure.UserMapper;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.organization.api.CreateEmployeeRequest;
import com.example.meeting.organization.api.DepartmentOptionView;
import com.example.meeting.organization.api.EmployeeItemView;
import com.example.meeting.organization.api.EmployeeListView;
import com.example.meeting.organization.api.ResetEmployeePasswordRequest;
import com.example.meeting.organization.api.UpdateEmployeeRequest;
import com.example.meeting.organization.api.UpdateEmployeeStatusRequest;
import com.example.meeting.organization.domain.Department;
import com.example.meeting.organization.infrastructure.DepartmentMapper;
import com.example.meeting.organization.infrastructure.EmployeeAdminMapper;
import com.example.meeting.organization.infrastructure.EmployeeAdminRow;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class EmployeeAdministrationService {

  private static final Set<String> ROLES = Set.of("EMPLOYEE", "ADMIN");
  private static final Set<String> STATUSES = Set.of("ACTIVE", "DISABLED");

  private final UserMapper userMapper;
  private final DepartmentMapper departmentMapper;
  private final EmployeeAdminMapper employeeMapper;
  private final PasswordEncoder passwordEncoder;
  private final Clock clock;

  public EmployeeAdministrationService(
      UserMapper userMapper,
      DepartmentMapper departmentMapper,
      EmployeeAdminMapper employeeMapper,
      PasswordEncoder passwordEncoder,
      Clock clock) {
    this.userMapper = userMapper;
    this.departmentMapper = departmentMapper;
    this.employeeMapper = employeeMapper;
    this.passwordEncoder = passwordEncoder;
    this.clock = clock;
  }

  public List<DepartmentOptionView> departments() {
    return employeeMapper.findActiveDepartments().stream()
        .map(
            department ->
                new DepartmentOptionView(
                    department.getId(),
                    department.getName(),
                    department.getDefaultBuilding(),
                    department.getDefaultFloor()))
        .toList();
  }

  public EmployeeListView list(
      String rawKeyword, Long departmentId, String rawRole, String rawStatus, int page, int size) {
    if (page < 1 || size < 1 || size > 100) {
      throw validation("page", "INVALID_PAGINATION", "分页参数超出允许范围");
    }
    String keyword = nullableLower(rawKeyword);
    String role = enumFilter(rawRole, ROLES, "role", "INVALID_EMPLOYEE_ROLE");
    String status = enumFilter(rawStatus, STATUSES, "status", "INVALID_EMPLOYEE_STATUS");
    if (departmentId != null) {
      requireActiveDepartment(departmentId);
    }
    long offset = (long) (page - 1) * size;
    List<EmployeeItemView> items =
        employeeMapper.findEmployees(keyword, departmentId, role, status, size, offset).stream()
            .map(this::view)
            .toList();
    return new EmployeeListView(
        items, employeeMapper.countEmployees(keyword, departmentId, role, status));
  }

  public EmployeeItemView get(long employeeId) {
    return view(requireEmployee(employeeId));
  }

  @Transactional
  public EmployeeItemView create(CreateEmployeeRequest request) {
    String username = request.username().trim().toLowerCase(Locale.ROOT);
    String email = request.email().trim().toLowerCase(Locale.ROOT);
    if (employeeMapper.countByUsername(username) > 0) {
      throw new BusinessException(ErrorCode.EMPLOYEE_USERNAME_CONFLICT);
    }
    if (employeeMapper.countByEmail(email) > 0) {
      throw new BusinessException(ErrorCode.EMPLOYEE_EMAIL_CONFLICT);
    }
    requireEnum(request.role(), ROLES, "role", "INVALID_EMPLOYEE_ROLE");
    requireEnum(request.status(), STATUSES, "status", "INVALID_EMPLOYEE_STATUS");
    requireActiveDepartmentIfPresent(request.departmentId());

    LocalDateTime now = LocalDateTime.now(clock);
    UserAccount account = new UserAccount();
    account.setUsername(username);
    account.setPasswordHash(passwordEncoder.encode(request.initialPassword()));
    account.setDisplayName(request.displayName().trim());
    account.setEmail(email);
    account.setDepartmentId(request.departmentId());
    account.setRole(request.role());
    account.setStatus(request.status());
    account.setCreatedAt(now);
    account.setUpdatedAt(now);
    account.setVersion(0);
    try {
      userMapper.insert(account);
    } catch (DataIntegrityViolationException exception) {
      throw uniqueConflict(exception);
    }
    return get(account.getId());
  }

  @Transactional
  public EmployeeItemView update(
      long employeeId, UpdateEmployeeRequest request, AuthenticatedUser actor) {
    requireEmployee(employeeId);
    requireEnum(request.role(), ROLES, "role", "INVALID_EMPLOYEE_ROLE");
    requireActiveDepartmentIfPresent(request.departmentId());
    if (employeeId == actor.userId() && !"ADMIN".equals(request.role())) {
      throw new BusinessException(ErrorCode.EMPLOYEE_STATE_CONFLICT);
    }
    String email = request.email().trim().toLowerCase(Locale.ROOT);
    if (employeeMapper.countOtherByEmail(email, employeeId) > 0) {
      throw new BusinessException(ErrorCode.EMPLOYEE_EMAIL_CONFLICT);
    }
    try {
      int updated =
          employeeMapper.updateEmployee(
              employeeId,
              request.displayName().trim(),
              email,
              request.departmentId(),
              request.role(),
              request.expectedVersion(),
              LocalDateTime.now(clock));
      assertUpdated(updated);
    } catch (DataIntegrityViolationException exception) {
      throw new BusinessException(ErrorCode.EMPLOYEE_EMAIL_CONFLICT);
    }
    return get(employeeId);
  }

  @Transactional
  public EmployeeItemView updateStatus(
      long employeeId, UpdateEmployeeStatusRequest request, AuthenticatedUser actor) {
    requireEmployee(employeeId);
    requireEnum(request.status(), STATUSES, "status", "INVALID_EMPLOYEE_STATUS");
    if (employeeId == actor.userId() && "DISABLED".equals(request.status())) {
      throw new BusinessException(ErrorCode.EMPLOYEE_STATE_CONFLICT);
    }
    assertUpdated(
        employeeMapper.updateStatus(
            employeeId, request.status(), request.expectedVersion(), LocalDateTime.now(clock)));
    return get(employeeId);
  }

  @Transactional
  public EmployeeItemView resetPassword(long employeeId, ResetEmployeePasswordRequest request) {
    requireEmployee(employeeId);
    assertUpdated(
        employeeMapper.updatePassword(
            employeeId,
            passwordEncoder.encode(request.newPassword()),
            request.expectedVersion(),
            LocalDateTime.now(clock)));
    return get(employeeId);
  }

  private EmployeeAdminRow requireEmployee(long employeeId) {
    return employeeMapper
        .findById(employeeId)
        .orElseThrow(() -> new BusinessException(ErrorCode.EMPLOYEE_NOT_FOUND));
  }

  private void requireActiveDepartmentIfPresent(Long departmentId) {
    if (departmentId != null) {
      requireActiveDepartment(departmentId);
    }
  }

  private void requireActiveDepartment(long departmentId) {
    Department department = departmentMapper.selectById(departmentId);
    if (department == null || !"ACTIVE".equals(department.getStatus())) {
      throw new BusinessException(ErrorCode.DEPARTMENT_NOT_FOUND);
    }
  }

  private void assertUpdated(int updated) {
    if (updated != 1) {
      throw new BusinessException(ErrorCode.EMPLOYEE_STATE_CONFLICT);
    }
  }

  private BusinessException uniqueConflict(DataIntegrityViolationException exception) {
    String message = exception.getMostSpecificCause().getMessage().toLowerCase(Locale.ROOT);
    if (message.contains("email")) {
      return new BusinessException(ErrorCode.EMPLOYEE_EMAIL_CONFLICT);
    }
    return new BusinessException(ErrorCode.EMPLOYEE_USERNAME_CONFLICT);
  }

  private String nullableLower(String value) {
    if (value == null || value.isBlank()) {
      return null;
    }
    return value.trim().toLowerCase(Locale.ROOT);
  }

  private String enumFilter(String value, Set<String> allowed, String field, String reason) {
    if (value == null || value.isBlank()) {
      return null;
    }
    String normalized = value.trim().toUpperCase(Locale.ROOT);
    requireEnum(normalized, allowed, field, reason);
    return normalized;
  }

  private void requireEnum(String value, Set<String> allowed, String field, String reason) {
    if (!allowed.contains(value)) {
      throw validation(field, reason, field + " 不是有效值");
    }
  }

  private EmployeeItemView view(EmployeeAdminRow row) {
    return new EmployeeItemView(
        row.getId(),
        row.getUsername(),
        row.getDisplayName(),
        row.getEmail(),
        row.getDepartmentId(),
        row.getDepartmentName(),
        row.getRole(),
        row.getStatus(),
        row.getVersion(),
        offset(row.getCreatedAt()),
        offset(row.getUpdatedAt()));
  }

  private OffsetDateTime offset(LocalDateTime value) {
    return value.atZone(clock.getZone()).toOffsetDateTime();
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}

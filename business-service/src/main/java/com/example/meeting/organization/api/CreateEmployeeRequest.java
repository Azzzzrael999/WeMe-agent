package com.example.meeting.organization.api;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record CreateEmployeeRequest(
    @NotBlank @Pattern(regexp = "[A-Za-z0-9._-]{3,64}") String username,
    @NotBlank @Size(min = 8, max = 72) String initialPassword,
    @NotBlank @Size(max = 64) String displayName,
    @NotBlank @Email @Size(max = 128) String email,
    Long departmentId,
    @NotBlank @Pattern(regexp = "EMPLOYEE|ADMIN") String role,
    @NotBlank @Pattern(regexp = "ACTIVE|DISABLED") String status) {}

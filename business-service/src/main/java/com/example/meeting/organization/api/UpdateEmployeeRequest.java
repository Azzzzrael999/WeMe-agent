package com.example.meeting.organization.api;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record UpdateEmployeeRequest(
    @NotBlank @Size(max = 64) String displayName,
    @NotBlank @Email @Size(max = 128) String email,
    Long departmentId,
    @NotBlank @Pattern(regexp = "EMPLOYEE|ADMIN") String role,
    @Min(0) int expectedVersion) {}

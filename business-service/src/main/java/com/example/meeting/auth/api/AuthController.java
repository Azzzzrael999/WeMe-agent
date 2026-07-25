package com.example.meeting.auth.api;

import com.example.meeting.auth.application.AuthenticationService;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

  private final AuthenticationService authenticationService;
  private final ApiResponseFactory responseFactory;

  public AuthController(
      AuthenticationService authenticationService, ApiResponseFactory responseFactory) {
    this.authenticationService = authenticationService;
    this.responseFactory = responseFactory;
  }

  @PostMapping("/login")
  public ApiSuccess<LoginResponse> login(
      @Valid @RequestBody LoginRequest loginRequest, HttpServletRequest request) {
    return responseFactory.success(authenticationService.login(loginRequest), request);
  }

  @GetMapping("/me")
  public ApiSuccess<UserView> me(
      @AuthenticationPrincipal AuthenticatedUser principal, HttpServletRequest request) {
    return responseFactory.success(authenticationService.currentUser(principal.userId()), request);
  }
}

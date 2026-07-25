package com.example.meeting.auth.application;

import com.example.meeting.auth.api.LoginRequest;
import com.example.meeting.auth.api.LoginResponse;
import com.example.meeting.auth.api.UserView;
import com.example.meeting.auth.domain.UserAccount;
import com.example.meeting.auth.infrastructure.UserMapper;
import com.example.meeting.auth.infrastructure.UserProfileRow;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.JwtService;
import java.util.List;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

@Service
public class AuthenticationService {

  private static final String DUMMY_PASSWORD_HASH =
      "$2b$10$GAHoUga.saN20ng9x5s6YeNjz8/l1/SXQrVJKIKuWgIwFu4BRO5s6";

  private final UserMapper userMapper;
  private final PasswordEncoder passwordEncoder;
  private final JwtService jwtService;

  public AuthenticationService(
      UserMapper userMapper, PasswordEncoder passwordEncoder, JwtService jwtService) {
    this.userMapper = userMapper;
    this.passwordEncoder = passwordEncoder;
    this.jwtService = jwtService;
  }

  public LoginResponse login(LoginRequest request) {
    UserAccount account = userMapper.findByUsername(request.username()).orElse(null);
    String passwordHash = account == null ? DUMMY_PASSWORD_HASH : account.getPasswordHash();
    boolean passwordMatches = passwordEncoder.matches(request.password(), passwordHash);
    if (account == null || !account.isActive() || !passwordMatches) {
      throw new BusinessException(ErrorCode.AUTH_REQUIRED, "用户名或密码错误");
    }

    UserView user = loadActiveUserView(account.getId());
    String accessToken = jwtService.issue(account.getId(), account.getUsername(), user.roles());
    return new LoginResponse(accessToken, "Bearer", jwtService.expirationSeconds(), user);
  }

  public UserView currentUser(long userId) {
    return loadActiveUserView(userId);
  }

  public UserAccount loadActiveAccount(String username) {
    return userMapper
        .findByUsername(username)
        .filter(UserAccount::isActive)
        .orElseThrow(() -> new BusinessException(ErrorCode.AUTH_REQUIRED));
  }

  private UserView loadActiveUserView(long userId) {
    UserProfileRow row =
        userMapper
            .findProfileById(userId)
            .filter(profile -> "ACTIVE".equals(profile.getStatus()))
            .orElseThrow(() -> new BusinessException(ErrorCode.AUTH_REQUIRED));
    return new UserView(
        row.getId(),
        row.getUsername(),
        row.getDisplayName(),
        row.getEmail(),
        row.getDepartmentId(),
        row.getDepartmentName(),
        List.of(row.getRole()));
  }
}

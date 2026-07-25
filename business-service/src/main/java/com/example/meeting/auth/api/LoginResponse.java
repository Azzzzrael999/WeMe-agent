package com.example.meeting.auth.api;

public record LoginResponse(String accessToken, String tokenType, long expiresIn, UserView user) {}

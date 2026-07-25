package com.example.meeting.common.config;

import java.time.Clock;
import java.time.DateTimeException;
import java.time.ZoneId;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class TimeConfiguration {

  @Bean
  Clock applicationClock(@Value("${app.timezone}") String timezone) {
    try {
      return Clock.system(ZoneId.of(timezone));
    } catch (DateTimeException exception) {
      throw new IllegalStateException("APP_TIMEZONE is invalid", exception);
    }
  }
}

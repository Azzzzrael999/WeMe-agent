package com.example.meeting;

import java.util.TimeZone;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.security.servlet.UserDetailsServiceAutoConfiguration;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication(exclude = UserDetailsServiceAutoConfiguration.class)
@ConfigurationPropertiesScan
@EnableScheduling
@MapperScan(
    basePackages = "com.example.meeting",
    annotationClass = org.apache.ibatis.annotations.Mapper.class)
public class MeetingApplication {

  public static void main(String[] args) {
    String timezone = System.getenv().getOrDefault("APP_TIMEZONE", "Asia/Shanghai");
    TimeZone.setDefault(TimeZone.getTimeZone(timezone));
    SpringApplication.run(MeetingApplication.class, args);
  }
}

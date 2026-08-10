package com.example.meeting.meeting.lifecycle.application;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class MeetingLifecycleScheduler {

  private final MeetingLifecycleScheduledWriter writer;

  public MeetingLifecycleScheduler(MeetingLifecycleScheduledWriter writer) {
    this.writer = writer;
  }

  @Scheduled(
      fixedDelayString = "${app.lifecycle.scan-interval-millis:60000}",
      initialDelayString = "${app.lifecycle.scan-interval-millis:60000}")
  public void scheduledScan() {
    writer.scan();
  }

  public MeetingLifecycleScheduledWriter.ScanResult scanNow() {
    return writer.scan();
  }
}

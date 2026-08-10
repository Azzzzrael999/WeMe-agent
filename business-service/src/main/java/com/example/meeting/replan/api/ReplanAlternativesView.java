package com.example.meeting.replan.api;

import java.util.List;

public record ReplanAlternativesView(
    long caseId,
    int caseVersion,
    int meetingVersion,
    boolean sameTime,
    List<String> changedConstraints,
    List<String> preservedConstraints,
    List<ReplanAlternativeView> items) {

  public ReplanAlternativesView {
    changedConstraints = List.copyOf(changedConstraints);
    preservedConstraints = List.copyOf(preservedConstraints);
    items = List.copyOf(items);
  }
}

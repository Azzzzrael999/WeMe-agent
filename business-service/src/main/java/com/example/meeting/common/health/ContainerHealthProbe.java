package com.example.meeting.common.health;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

public final class ContainerHealthProbe {

  private ContainerHealthProbe() {}

  public static void main(String[] args) {
    try (HttpClient client =
        HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build()) {
      HttpRequest request =
          HttpRequest.newBuilder()
              .uri(URI.create("http://127.0.0.1:8080/actuator/health/readiness"))
              .timeout(Duration.ofSeconds(3))
              .GET()
              .build();
      HttpResponse<Void> response = client.send(request, HttpResponse.BodyHandlers.discarding());
      if (response.statusCode() < 200 || response.statusCode() >= 300) {
        System.exit(1);
      }
    } catch (Exception exception) {
      System.exit(1);
    }
  }
}

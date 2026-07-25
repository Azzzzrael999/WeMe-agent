ARG MAVEN_IMAGE=maven:3.9.11-eclipse-temurin-21
ARG JAVA_RUNTIME_IMAGE=eclipse-temurin:21-jre-jammy

FROM ${MAVEN_IMAGE} AS build
WORKDIR /workspace
COPY .mvn/ .mvn/
COPY mvnw pom.xml ./
RUN chmod +x mvnw && ./mvnw -B -ntp dependency:go-offline
COPY src/ src/
RUN ./mvnw -B -ntp verify

FROM ${JAVA_RUNTIME_IMAGE} AS runtime
RUN groupadd --gid 10001 meeting \
    && useradd --uid 10001 --gid meeting --create-home --shell /usr/sbin/nologin meeting
WORKDIR /opt/app
COPY --from=build --chown=meeting:meeting /workspace/target/business-service.jar ./business-service.jar
COPY --from=build --chown=meeting:meeting \
    /workspace/target/classes/com/example/meeting/common/health/ContainerHealthProbe.class \
    /opt/meeting-health/com/example/meeting/common/health/ContainerHealthProbe.class
USER 10001:10001
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75.0 -XX:+ExitOnOutOfMemoryError -Duser.timezone=Asia/Shanghai"
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
    CMD ["java", "-cp", "/opt/meeting-health", "com.example.meeting.common.health.ContainerHealthProbe"]
ENTRYPOINT ["java", "-jar", "/opt/app/business-service.jar"]

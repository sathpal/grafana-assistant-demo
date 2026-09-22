# syntax=docker/dockerfile:1.7
# cortex-publisher-service — Java 21 / Spring Boot 3.4, instrumented with the OpenTelemetry Java agent.
FROM maven:3.9.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -DskipTests dependency:resolve-plugins dependency:resolve || true
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -DskipTests package && cp target/*.jar /app.jar

FROM eclipse-temurin:21-jre
ADD https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v2.10.0/opentelemetry-javaagent.jar /otel/opentelemetry-javaagent.jar
COPY --from=build /app.jar /app/app.jar
ENV JAVA_TOOL_OPTIONS="-javaagent:/otel/opentelemetry-javaagent.jar -Xmx256m -XX:+UseG1GC"
EXPOSE 8085
ENTRYPOINT ["java", "-jar", "/app/app.jar"]

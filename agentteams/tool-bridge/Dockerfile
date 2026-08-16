FROM python:3.12-alpine

RUN addgroup -S bridge && adduser -S -D -H -u 10001 -G bridge bridge
COPY src/orgrebase/vertex_tool_bridge.py /app/vertex_tool_bridge.py
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "/app/vertex_tool_bridge.py"]

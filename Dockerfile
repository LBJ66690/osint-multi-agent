FROM node:24-bookworm-slim AS travel_node
WORKDIR /mcp
COPY scripts/travel_eval/mcp/package*.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OSINT_STORAGE=mysql
WORKDIR /service
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system osint \
    && useradd --system --gid osint osint \
    && install -d -o osint -g osint /service/outputs
COPY *.py ./
COPY --from=travel_node /usr/local/bin/node /usr/local/bin/node
COPY --from=travel_node /mcp/node_modules ./scripts/travel_eval/mcp/node_modules
COPY skills/travel/ ./skills/travel/
COPY static/ ./static/
USER osint
EXPOSE 8765
HEALTHCHECK --interval=20s --timeout=6s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4).close()"]
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1", "--timeout-graceful-shutdown", "15", "--no-access-log"]

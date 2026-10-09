FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml server.py ./

RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home mcp

USER mcp

EXPOSE 8000

CMD ["fastmcp", "run", "server.py", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]

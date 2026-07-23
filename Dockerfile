FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY server.py .

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["fastmcp", "run", "server.py", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]

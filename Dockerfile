FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

RUN uv pip install --system --no-cache .

ENTRYPOINT ["mcp-retroarch"]

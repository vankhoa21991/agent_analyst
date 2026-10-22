# Chainlit UI for the analyst agent.
#
# Build:  docker build -t analyst-ui .
# Run:    docker run --rm -p 8000:8000 \
#            -e ANALYST_API_KEY=... \
#            -e ANALYST_BASE_URL=http://host.docker.internal:8080/v1 \
#            -v $(pwd)/data:/app/data analyst-ui
#
# The LLM server (llama-server) is expected on the host; override
# ANALYST_BASE_URL to reach it. The data volume holds olist.sqlite,
# scratch.sqlite and the RAG index.

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY src ./src
COPY agent.yaml ./
COPY data ./data

RUN uv pip install --system --no-cache .

ENV CHAINLIT_HOST=0.0.0.0 \
    CHAINLIT_PORT=8000

EXPOSE 8000

CMD ["chainlit", "run", "src/analyst/ui/app.py", "--host", "0.0.0.0", "--port", "8000"]

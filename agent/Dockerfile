FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /srv/agent
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

ENV PATH="/srv/agent/.venv/bin:$PATH"
EXPOSE 8100
CMD ["uvicorn", "settleiq_agent.main:app", "--host", "0.0.0.0", "--port", "8100"]

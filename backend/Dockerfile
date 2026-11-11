FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /srv/app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

ENV PATH="/srv/app/.venv/bin:$PATH"
# The capability manifest is generated from the code in this image (spec 9.7.1); startup
# refuses a manifest that differs, so a stale committed copy cannot be packaged.
RUN python -m app.release.capability_manifest --write
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /srv/shop
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY shop ./shop

ENV PATH="/srv/shop/.venv/bin:$PATH"
USER nobody
EXPOSE 8000
CMD ["uvicorn", "shop.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

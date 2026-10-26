# Build frontend in Docker: Windows host does not need Node.js.
FROM node:22-alpine AS admin-build
WORKDIR /admin-ui
COPY admin-ui/package.json ./
RUN npm install --no-audit --no-fund
COPY admin-ui/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM python:3.14.8-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project
COPY . .
COPY --from=admin-build /admin-ui/dist /srv/app/admin-dist
RUN adduser --disabled-password --gecos '' appuser
RUN mkdir -p /srv/app/media && chown -R appuser:appuser /srv/app/media
USER appuser
EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

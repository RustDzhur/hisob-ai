FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
RUN pip install --no-cache-dir uv==0.12.19
WORKDIR /srv
COPY pyproject.toml uv.lock ./
# Окружение разработки: включает dev-группу (pytest, ruff, mypy).
RUN uv sync --frozen
ENV PATH="/opt/venv/bin:$PATH"
COPY . .
RUN useradd --system --uid 10001 app && chown -R app /srv
USER app
EXPOSE 8000

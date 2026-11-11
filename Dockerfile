FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
COPY backend ./backend
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY alembic.ini ./
COPY migrations ./migrations
COPY tools/pass0 ./tools/pass0
CMD ["uvicorn", "interlock.api:app", "--host", "0.0.0.0", "--port", "8000"]

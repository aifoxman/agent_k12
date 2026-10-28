FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src/k12_server ./src/k12_server
RUN pip install --no-cache-dir . && useradd --system --uid 10001 app && mkdir /data && chown app /data
USER app
ENV DATABASE_PATH=/data/homework.sqlite3
EXPOSE 8000
CMD ["uvicorn", "k12_server.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

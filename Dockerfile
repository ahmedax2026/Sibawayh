FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m app && mkdir /data && chown app:app /data
COPY server ./server
USER app
ENV SIBAWAYH_DB=/data/sibawayh.db
EXPOSE 8000
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]

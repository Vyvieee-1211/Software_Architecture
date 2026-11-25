FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY frontend ./frontend
RUN mkdir -p /data

EXPOSE 8000
# PORT do nền tảng host đặt (Render), mặc định 8000. SEED_ON_START=true nạp dữ liệu mẫu lúc khởi
# động (seed tự bỏ qua nếu đã có concert). --proxy-headers: sau proxy HTTPS của Render, IP thật
# của khách nằm trong X-Forwarded-For; thiếu nó, rate limit theo IP coi mọi khách là một người.
ENV PYTHONIOENCODING=utf-8
CMD ["sh", "-c", "if [ \"$SEED_ON_START\" = \"true\" ]; then python -m scripts.seed; fi; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]

FROM python:3.10-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.10-slim

WORKDIR /app

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

COPY . .

# Playwright system dependencies for Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
      libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
      libcairo2 libasound2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m playwright install chromium

RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python3", "main.py"]

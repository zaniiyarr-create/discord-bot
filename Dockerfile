FROM python:3.10-slim

WORKDIR /app

# دامەزراندنا بنەمایێن سیستەمی و libsodium بۆ دەنگی
RUN apt-get update && apt-get install -y \
    build-essential \
    libsodium-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# دامەزراندنا پاکێجێن پایتۆنی
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]

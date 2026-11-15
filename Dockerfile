FROM python:3.10-slim

WORKDIR /app

# دامەزراندنا پاکێجێن سیستەمی یێن پێدڤی بۆ دەنگی
RUN apt-get update && apt-get install -y \
    libsodium-dev \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# گوهەستنا فایلێن پڕۆژەی
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# کارپێکرنا بۆتی
CMD ["python", "bot.py"]

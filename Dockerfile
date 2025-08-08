# Gunakan Python 3.10 (lebih stabil untuk TA-Lib)
FROM python:3.10-slim

# Install dependencies TA-Lib
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    wget \
    && wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz \
    && tar -xzf ta-lib-0.4.0-src.tar.gz \
    && cd ta-lib && ./configure --prefix=/usr && make && make install \
    && cd .. && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Set workdir
WORKDIR /app

# Copy semua file ke container
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Jalankan bot
CMD ["python", "bot.py"]

# Dockerfile — build environment with TA-Lib compiled from source
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# Install OS-level build deps required by TA-Lib and common utils
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    curl \
    unzip \
    libz-dev \
    libbz2-dev \
    libssl-dev \
    libffi-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install TA-Lib from source (v0.4.0)
RUN wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz -O /tmp/ta-lib.tar.gz \
    && mkdir -p /tmp/ta-lib-src \
    && tar -xzf /tmp/ta-lib.tar.gz -C /tmp/ta-lib-src --strip-components=1 \
    && cd /tmp/ta-lib-src && ./configure --prefix=/usr && make && make install \
    && rm -rf /tmp/ta-lib.tar.gz /tmp/ta-lib-src

# Copy requirements and install Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy source
COPY . /app

ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8

# Run bot
CMD ["python", "bot.py"]

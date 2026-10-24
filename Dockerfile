FROM apify/actor-python:3.11

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    build-essential \
    python3-dev \
    libboost-dev \
    libboost-system-dev \
    libboost-python-dev \
    libboost-filesystem-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY ./src ./src

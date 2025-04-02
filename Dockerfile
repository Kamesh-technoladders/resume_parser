FROM python:3.9-slim

# Install system dependencies for pdf2image, pytesseract, and redis-cli with retries
RUN apt-get update --fix-missing || (sleep 5 && apt-get update --fix-missing) && \
    apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    redis-tools \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Make start.sh executable
COPY start.sh .
COPY .env .
RUN chmod +x start.sh

EXPOSE 5005
CMD ["./start.sh"]
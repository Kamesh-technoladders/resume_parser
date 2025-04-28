FROM python:3.9-slim

# Set environment variables to prevent interactive prompts during apt-get install
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies for pdf2image, pytesseract, and redis-cli with retries
RUN apt-get update --fix-missing || (sleep 5 && apt-get update --fix-missing) && \
    apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        redis-tools \
        libreoffice \
        fonts-freefont-ttf \ 
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Make start.sh executable

RUN chmod +x container.sh

EXPOSE 5005
# Set the command to run the script that starts Flask and RQ INSIDE the container
CMD ["./container.sh"]
FROM python:3.10-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt /app/

# Install dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application files
COPY . /app

# Run the bot
CMD ["python3", "scraper.py"]

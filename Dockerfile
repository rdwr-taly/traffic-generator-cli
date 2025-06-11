FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1


# Set work directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy in your Python code
COPY container_control.py .
COPY traffic_generator.py .

# Expose port 8080 for the single Flask app
EXPOSE 8080

# Run container_control (Flask) on port 8080
CMD ["python", "-m", "uvicorn", "container_control:app", "--host", "0.0.0.0", "--port", "8080"]
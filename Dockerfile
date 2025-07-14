FROM python:3.11-slim

# Set environment variables
ENV TZ=UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system packages required for container control and networking
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      iproute2 \
      iptables \
      sudo \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
ARG APP_USER=app_user
RUN useradd -ms /bin/bash ${APP_USER} && \
    echo "${APP_USER} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Set work directory
WORKDIR /app

# Clone Container Control Core from GitHub
RUN git clone --branch v1.0.0 --depth 1 https://github.com/rdwr-taly/container-control.git /tmp/container-control && \
    cp /tmp/container-control/container_control_core.py . && \
    cp /tmp/container-control/app_adapter.py . && \
    rm -rf /tmp/container-control

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir fastapi uvicorn psutil ruamel.yaml

# Copy application source code
COPY traffic_generator.py .
COPY traffic_generator_adapter.py .
COPY config.yaml .

# Expose port 8080 for the Container Control API
EXPOSE 8080

# Run with Container Control Core (requires CAP_NET_ADMIN for traffic control)
CMD ["python", "-m", "uvicorn", "container_control_core:app", "--host", "0.0.0.0", "--port", "8080"]
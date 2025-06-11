# Traffic Generator CLI

A sophisticated HTTP traffic generation tool designed to simulate realistic web and API traffic. This application is designed to be controlled by external systems (such as "show runner") via REST API endpoints.

## Overview

The Traffic Generator CLI is a containerized Python application that generates realistic HTTP traffic patterns for testing, load simulation, and performance analysis. It supports both web browser simulation and API client simulation with extensive configuration options.

## Architecture

### Core Components

- **`container_control.py`** - FastAPI web service that provides REST API control interface
- **`traffic_generator.py`** - Core async traffic generation engine
- **`Dockerfile`** - Container configuration for deployment
- **`requirements.txt`** - Python dependencies

### Key Features

#### Traffic Simulation Types
- **Web Browser Simulation**: Mimics real browser behavior with varied user agents, headers, and request patterns
- **API Client Simulation**: Simulates programmatic API access with appropriate headers and request patterns

#### Authentication Support
- **Basic Authentication**: Username/password authentication
- **Bearer Token**: JWT and other token-based authentication
- **Form Data**: Login form submission
- **JSON Body**: Authentication via JSON payload
- **Query Parameters**: Authentication via URL parameters
- **Custom Headers**: Custom authentication header support

#### Advanced Features
- **Variable Substitution**: Dynamic content using `@variable` placeholders in paths and request bodies
- **Session Management**: Simulates user sessions with configurable duration
- **Rate Limiting**: Configurable concurrent request limits
- **DNS Override**: Target specific IP addresses while maintaining proper Host headers
- **Real-time Metrics**: RPS tracking, system monitoring, and Prometheus metrics
- **Realistic Headers**: Extensive user-agent rotation and header variation

## API Endpoints

### Control Endpoints

#### Start Traffic Generation
```http
POST /api/start
Content-Type: application/json

{
  "config": {
    "Traffic Generator URL": "https://example.com",
    "Traffic Generator DNS Override": "192.168.1.100",
    "XFF Header Name": "X-Forwarded-For",
    "Rate Limit": 10,
    "Simulated Users": 5,
    "Minimum Session Length": 30,
    "Maximum Session Length": 300,
    "Debug": false
  },
  "sitemap": {
    "has_auth": true,
    "paths": [...],
    "auth": {...},
    "variables": {...}
  }
}
```

The endpoint is backward compatible with the older format where the configuration
keys appear at the top level:

```json
{
  "Traffic Generator URL": "https://example.com",
  "Traffic Generator DNS Override": "192.168.1.100",
  "XFF Header Name": "X-Forwarded-For",
  "Rate Limit": 10,
  "Simulated Users": 5,
  "Minimum Session Length": 30,
  "Maximum Session Length": 300,
  "sitemap": { ... }
}
```

Additionally, if the `sitemap` object includes metadata (e.g. `id`, `name`, and a
nested `sitemap` field), the inner `sitemap` is automatically extracted.

#### Stop Traffic Generation
```http
POST /api/stop
```

#### Health Check
```http
GET /api/health
```

### Metrics Endpoints

#### JSON Metrics
```http
GET /api/metrics
```

Returns:
```json
{
  "timestamp": "2025-06-11T10:30:00Z",
  "app_status": "running",
  "container_status": "running",
  "network": {
    "bytes_sent": 1024000,
    "bytes_recv": 2048000,
    "packets_sent": 1500,
    "packets_recv": 2000
  },
  "system": {
    "cpu_percent": 25.5,
    "memory_percent": 45.2,
    "memory_available_mb": 2048.5,
    "memory_used_mb": 1024.8
  },
  "metrics": {
    "rps": 12.5
  }
}
```

#### Prometheus Metrics
```http
GET /metrics
```

Returns Prometheus-formatted metrics for monitoring and alerting.

## Configuration

### Container Config
- **Traffic Generator URL**: Target URL for traffic generation
- **Traffic Generator DNS Override**: Optional IP address to override DNS resolution
- **XFF Header Name**: Header name for X-Forwarded-For simulation
- **Rate Limit**: Maximum concurrent requests
- **Simulated Users**: Number of concurrent user sessions
- **Minimum/Maximum Session Length**: Session duration range in seconds
- **Debug**: Enable debug logging

### Site Map Configuration

#### Path Definitions
```json
{
  "method": "GET|POST|PUT|DELETE",
  "paths": ["/api/users", "/api/products"],
  "body": "optional request body",
  "traffic_type": "web|api"
}
```

#### Authentication Configuration
```json
{
  "auth_method": "login_path",
  "auth_path": "/api/auth/login",
  "auth_type": "basic|bearer|form_data|json_body|query_params|custom_headers",
  "credentials": {
    "header": {"Authorization": "Bearer token"},
    "body_params": {"username": "user", "password": "pass"},
    "json_body": {"email": "user@example.com", "password": "pass"}
  }
}
```

#### Variable Definitions
```json
{
  "variables": {
    "user_id": {
      "type": "list",
      "value": ["123", "456", "789"]
    },
    "product_name": {
      "type": "list", 
      "value": ["widget", "gadget", "tool"]
    }
  }
}
```

Variables can be used in paths and request bodies using `@variable_name` syntax.

## Deployment

### Docker Build
```bash
docker build -t traffic-generator .
```

### Docker Run
```bash
docker run -d -p 8080:8080 --name traffic-gen traffic-generator
```

### Environment Variables
- **MEMORY_SOFT_LIMIT**: Soft memory limit in MB (default: 4096)
- **MEMORY_HARD_LIMIT**: Hard memory limit in MB (default: 4608)

## Usage Examples

### Basic Web Traffic
```bash
curl -X POST http://localhost:8080/api/start \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "Traffic Generator URL": "https://example.com",
      "Rate Limit": 5,
      "Simulated Users": 3,
      "Minimum Session Length": 60,
      "Maximum Session Length": 180
    },
    "sitemap": {
      "has_auth": false,
      "paths": [
        {
          "method": "GET",
          "paths": ["/", "/about", "/products"],
          "traffic_type": "web"
        }
      ]
    }
  }'
```

### API Traffic with Authentication
```bash
curl -X POST http://localhost:8080/api/start \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "Traffic Generator URL": "https://api.example.com",
      "Rate Limit": 10,
      "Simulated Users": 5,
      "Minimum Session Length": 120,
      "Maximum Session Length": 300
    },
    "sitemap": {
      "has_auth": true,
      "auth": {
        "auth_method": "login_path",
        "auth_path": "/api/auth/login",
        "auth_type": "json_body",
        "credentials": {
          "json_body": {
            "email": "test@example.com",
            "password": "password123"
          }
        }
      },
      "paths": [
        {
          "method": "GET",
          "paths": ["/api/users/@user_id", "/api/orders"],
          "traffic_type": "api"
        }
      ],
      "variables": {
        "user_id": {
          "type": "list",
          "value": ["123", "456", "789"]
        }
      }
    }
  }'
```

## Monitoring

### Application Status
- **initializing**: Application starting up
- **running**: Traffic generation active
- **stopping**: Graceful shutdown in progress
- **stopped**: No traffic generation

### Key Metrics
- **RPS**: Real-time requests per second
- **CPU/Memory**: System resource utilization
- **Network**: Bytes and packets sent/received
- **Container Status**: Overall container health

## Integration with Show Runner

This application is designed to be controlled by an external "show runner" application. The show runner can:

1. Start traffic generation with specific configurations
2. Monitor real-time metrics and performance
3. Stop traffic generation gracefully
4. Collect Prometheus metrics for analysis

### Communication Flow
1. Show runner sends configuration via `POST /api/start`
2. Traffic generator validates configuration and starts generating traffic
3. Show runner polls `GET /api/metrics` for real-time monitoring
4. Show runner can stop traffic via `POST /api/stop` when needed

## Development

### Requirements
- Python 3.11+
- FastAPI
- aiohttp
- Pydantic
- psutil
- uvicorn

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python container_control.py
```

### Testing
The application provides comprehensive logging and metrics for testing and debugging. Enable debug mode in the configuration for detailed request/response logging.

## Security Considerations

- The application is designed to run in controlled environments
- Authentication credentials are handled securely in memory
- Rate limiting prevents resource exhaustion
- Graceful shutdown ensures clean termination
- Memory limits prevent excessive resource usage

## Performance

- Async/await architecture for high concurrency
- Configurable rate limiting and session management
- Real-time metrics with minimal overhead
- Efficient memory usage with rolling metrics windows
- Optimized for container deployment

## Troubleshooting

### Common Issues

1. **High Memory Usage**: Adjust `MEMORY_SOFT_LIMIT` and `Rate Limit` settings
2. **Connection Errors**: Verify target URL and DNS override settings
3. **Authentication Failures**: Check credentials and auth configuration
4. **Low RPS**: Increase `Rate Limit` and `Simulated Users`

### Logs
Application logs provide detailed information about:
- Traffic generation start/stop events
- Authentication attempts and results
- Request failures and errors
- System resource usage
- Configuration validation issues

## License

This project is designed for internal use and testing purposes.
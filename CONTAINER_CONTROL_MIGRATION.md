# Traffic Generator CLI - Container Control v2.0 Integration

This project has been migrated to use the new **Container Control Core v2.0** system. This provides a standardized interface for containerized workloads with enhanced monitoring, lifecycle management, and optional services.

## Migration Summary

### What Changed

1. **New Architecture**: Now uses the Container Control Core as the main entry point instead of the custom `container_control.py`
2. **Adapter Pattern**: Created `traffic_generator_adapter.py` that implements the `ApplicationAdapter` interface
3. **Standardized API**: All endpoints now follow the Container Control API specification
4. **Enhanced Configuration**: Uses declarative `config.yaml` for adapter and service configuration
5. **Optional Services**: Can leverage built-in services for metrics, traffic control, and privileged operations

### What Stayed the Same

- **Core Traffic Generation Logic**: The `traffic_generator.py` module remains unchanged
- **Payload Structure**: The same request format is supported for backward compatibility
- **Functionality**: All traffic generation features work exactly as before

## API Endpoints

| Method | Path          | Description                                        |
|--------|---------------|----------------------------------------------------|
| `GET`  | `/api/health` | Health check endpoint                              |
| `POST` | `/api/start`  | Start traffic generation with configuration        |
| `POST` | `/api/stop`   | Stop traffic generation gracefully                |
| `POST` | `/api/update` | Update configuration (requires restart)           |
| `GET`  | `/api/metrics`| Get detailed metrics including traffic stats      |
| `GET`  | `/metrics`    | Prometheus-compatible metrics endpoint             |

## Usage

### Starting Traffic Generation

```bash
curl -X POST http://localhost:8080/api/start \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "Traffic Generator URL": "https://example.com",
      "XFF Header Name": "X-Forwarded-For", 
      "Rate Limit": 50,
      "Simulated Users": 10,
      "Minimum Session Length": 30,
      "Maximum Session Length": 120,
      "Debug": false
    },
    "sitemap": {
      "has_auth": false,
      "paths": [
        {
          "method": "GET",
          "paths": ["/", "/api/data", "/api/status"],
          "traffic_type": "api"
        }
      ],
      "global_headers": {
        "User-Agent": "TrafficGenerator/2.0"
      }
    }
  }'
```

### Getting Metrics

```bash
curl http://localhost:8080/api/metrics
```

Example response:
```json
{
  "timestamp": "2025-01-11T10:30:00.000Z",
  "app_status": "running",
  "container_status": "running", 
  "traffic_generator_status": "running",
  "current_rps": 45.2,
  "running": true,
  "simulated_users": 10,
  "rate_limit": 50,
  "target_url": "https://example.com",
  "network_bytes_sent": 1048576,
  "network_bytes_received": 2097152
}
```

## Configuration

The `config.yaml` file controls:

- **Adapter Settings**: Which adapter to load and security settings
- **Core Services**: Optional built-in services for metrics, traffic control, etc.
- **System Optimization**: Privileged commands for network tuning

Key configuration options:

```yaml
adapter:
  class: traffic_generator_adapter.TrafficGeneratorAdapter
  primary_payload_key: config
  run_as_user: app_user

metrics:
  network_monitoring:
    enabled: true
    interface: "eth0"

privileged_commands:
  pre_start:
    - ["sysctl", "-w", "net.core.somaxconn=65536"]
    # ... other network optimizations
```

## Building and Running

### Docker Build

```bash
docker build -t traffic-generator-cli .
```

### Run Container

```bash
docker run -d -p 8080:8080 \
  --cap-add=NET_ADMIN \
  --name traffic-generator \
  traffic-generator-cli
```

**Note**: `--cap-add=NET_ADMIN` is required if using the traffic control service for network shaping.

### Testing

Run the integration test script:

```bash
./test_integration.sh
```

## Benefits of Container Control v2.0

1. **Standardized Interface**: Same API across all containerized tools
2. **Enhanced Monitoring**: Built-in metrics collection and Prometheus export
3. **Security**: Privilege separation and controlled execution
4. **Observability**: Structured logging and health checks
5. **Flexibility**: Optional services reduce boilerplate code
6. **Operational**: Ready for Kubernetes with proper health/readiness probes

## Backward Compatibility

The migration maintains backward compatibility:
- Same payload structure for `/api/start`
- Same traffic generation behavior
- Same configuration options for the traffic generator itself

## Migration from v1.0

If you're upgrading from the old `container_control.py` system:

1. Update your Docker build to use the new Dockerfile
2. Update API calls to use the new endpoints (if different)
3. Monitor metrics format changes in `/api/metrics` response
4. Test with the provided integration script

The core traffic generation functionality remains identical.

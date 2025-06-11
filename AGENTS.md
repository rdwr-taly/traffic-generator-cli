# Contribution Guidelines

This repository does not enforce a strict style checker but aims for readable Python 3.11 code. Contributors should format code with [Black](https://black.readthedocs.io/en/stable/) before committing:

```bash
black .
```

## Environment Setup

Develop locally using a Python virtual environment. A sample setup script is provided in `setup_env.sh` which installs dependencies and exports recommended environment variables used by the Docker container (`PYTHONDONTWRITEBYTECODE` and `PYTHONUNBUFFERED`). Run it before starting the app:

```bash
bash setup_env.sh
```

The application can then be launched with:

```bash
uvicorn container_control:app --host 0.0.0.0 --port 8080
```

## Testing

No automated tests are included, but you can verify the running service by calling the `/api/health` endpoint:

```bash
curl http://localhost:8080/api/health
```

A successful response looks like:

```json
{"status": "healthy"}
```


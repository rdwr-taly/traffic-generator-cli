#!/bin/bash

# Test script for Traffic Generator CLI with Container Control integration

echo "Building Docker image..."
docker build -t traffic-generator-cli .

if [ $? -ne 0 ]; then
    echo "Failed to build Docker image"
    exit 1
fi

echo "Starting container..."
docker run -d --name traffic-gen-test -p 8080:8080 traffic-generator-cli

if [ $? -ne 0 ]; then
    echo "Failed to start container"
    exit 1
fi

echo "Waiting for container to be ready..."
sleep 5

echo "Testing health endpoint..."
curl -s http://localhost:8080/api/health

echo -e "\n\nTesting basic traffic generation..."
curl -X POST http://localhost:8080/api/start \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "Traffic Generator URL": "https://httpbin.org",
      "XFF Header Name": "X-Forwarded-For",
      "Rate Limit": 10,
      "Simulated Users": 2,
      "Minimum Session Length": 5,
      "Maximum Session Length": 15,
      "Debug": true
    },
    "sitemap": {
      "has_auth": false,
      "paths": [
        {
          "method": "GET",
          "paths": ["/get", "/headers", "/user-agent"],
          "traffic_type": "api"
        }
      ],
      "global_headers": {
        "User-Agent": "TrafficGenerator/1.0"
      }
    }
  }'

echo -e "\n\nWaiting 10 seconds for traffic generation..."
sleep 10

echo "Getting metrics..."
curl -s http://localhost:8080/api/metrics | jq .

echo -e "\n\nStopping traffic generation..."
curl -X POST http://localhost:8080/api/stop

echo -e "\n\nCleaning up..."
docker stop traffic-gen-test
docker rm traffic-gen-test

echo "Test completed!"

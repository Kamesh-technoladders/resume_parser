#!/bin/bash

# Define network name
NETWORK_NAME="resume-parser-network"

# Define container names
REDIS_CONTAINER="redis"
APP_CONTAINER="resume-parser-container"

# Define image names
APP_IMAGE="resume_parser_app"

# Create Docker network (if not already created)
echo "Creating Docker network: $NETWORK_NAME (if not exists)..."
docker network create $NETWORK_NAME || true

# Run Redis container
echo "Starting Redis container..."
docker run -d \
  --name $REDIS_CONTAINER \
  --network $NETWORK_NAME \
  redis \
  redis-server --bind 0.0.0.0 --protected-mode no

# Run Resume Parser application container
echo "Starting Resume Parser container..."
docker run -d \
  -p 5005:5005 \
  --name $APP_CONTAINER \
  --network $NETWORK_NAME \
  --env-file .env \
  $APP_IMAGE

echo "All containers are up and running!"

#!/bin/bash

# Define network name
NETWORK_NAME="resume-parser-network"

# Define container names
REDIS_CONTAINER="redis"
APP_CONTAINER="resume-parser-container" # Name of your app container

# Define image name
APP_IMAGE="resume_parser_app" # Name of the image to build

# --- Ensure Script is Run from Project Root ---
cd "$(dirname "$0")" || exit 1 # Change to the directory where the script is located

# Create Docker network (if not already created)
echo ">>> Creating Docker network: $NETWORK_NAME (if not exists)..."
docker network create $NETWORK_NAME || true

# Build the application image using the CORRECTED Dockerfile
echo ">>> Building the Docker image: $APP_IMAGE..."
# Use --no-cache during debugging if needed, otherwise remove it for faster builds
docker build --no-cache -t $APP_IMAGE .
if [ $? -ne 0 ]; then
    echo "!!! Docker build failed. Exiting."
    exit 1
fi

# Stop and remove existing containers (ensures clean start)
echo ">>> Stopping and removing existing containers..."
docker stop $APP_CONTAINER || true
docker rm $APP_CONTAINER || true
docker stop $REDIS_CONTAINER || true
docker rm $REDIS_CONTAINER || true

# Run Redis container
echo ">>> Starting Redis container ($REDIS_CONTAINER)..."
docker run -d \
  --name $REDIS_CONTAINER \
  --network $NETWORK_NAME \
  redis \
  redis-server --bind 0.0.0.0 --protected-mode no
if [ $? -ne 0 ]; then
    echo "!!! Failed to start Redis container. Exiting."
    exit 1
fi

# --- Optional Delay: Uncomment ONLY if you suspect Redis needs more time ---
# echo ">>> Waiting 5 seconds for Redis to initialize..."
# sleep 5
# ---

# Run Resume Parser application container using the image built with the corrected Dockerfile
echo ">>> Starting Resume Parser container ($APP_CONTAINER)..."
docker run -d \
  -p 5005:5005 \
  --name $APP_CONTAINER \
  --network $NETWORK_NAME \
  --env-file .env \
  $APP_IMAGE
  # The container will now automatically run entrypoint.sh (or container.sh)
  # because of the corrected CMD in the Dockerfile
if [ $? -ne 0 ]; then
    echo "!!! Failed to start Resume Parser container. Exiting."
    exit 1
fi

echo ""
echo ">>> Containers should be starting up!"
echo ">>> Use 'docker ps' to check their status."
echo ">>> Use 'docker logs $APP_CONTAINER -f' to view application logs."
echo ">>> Use 'docker logs $REDIS_CONTAINER' to view Redis logs if needed."
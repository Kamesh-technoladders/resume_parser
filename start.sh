#!/bin/bash

# Wait for Redis to be available
echo "Waiting for Redis to be available..."
while ! redis-cli -h redis -p 6379 ping > /dev/null 2>&1; do
    echo "Redis not available yet, waiting..."
    sleep 1
done
echo "Redis is available!"

# Start the Flask app in the background
python app.py &

# Start the RQ worker in the background with the correct Redis URL and redirect logs
rq worker --with-scheduler --url redis://redis:6379 >> /app/rq_worker.log 2>&1 &

# Keep the container running by tailing the logs
tail -f /app/rq_worker.log
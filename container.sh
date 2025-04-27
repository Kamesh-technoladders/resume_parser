#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Wait for Redis to be available
echo ">>> Waiting for Redis service at host 'redis' on port 6379..."
while ! redis-cli -h redis -p 6379 ping > /dev/null 2>&1; do
    echo "Redis not available yet, sleeping for 1 second..."
    sleep 1
done
echo ">>> Redis is available!"

# Start the Flask app in the background
echo ">>> Starting Flask application..."
python app.py &
FLASK_PID=$! # Get Flask process ID

# Start the RQ worker in the background, logging to stdout/stderr
# (Docker logs will capture this automatically)
echo ">>> Starting RQ worker..."
rq worker --with-scheduler --url redis://redis:6379 &
RQ_PID=$! # Get RQ worker process ID

echo ">>> Flask and RQ Worker started."
echo ">>> Tailing RQ Worker logs (use 'docker logs <container_id>' for combined logs)..."

# Wait for either process to exit
# This is a simple way to keep the script running while background jobs run.
# If Flask or RQ worker exits, the script will exit, stopping the container.
wait -n $FLASK_PID $RQ_PID

# Capture exit code
EXIT_CODE=$?
echo ">>> A background process exited with code $EXIT_CODE. Stopping container..."
exit $EXIT_CODE

# --- Alternative to keep container running (less informative on failure) ---
# Keep the container alive indefinitely - allows checking logs even if workers stop
# echo ">>> Processes started. Keeping container alive."
# tail -f /dev/null
# ---
from flask import Flask, request, jsonify
from flask_cors import CORS
from rq.job import Job
import tasks
from config import queue, redis_conn, supabase
import logging

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/api/validate-candidate', methods=['POST'])
def validate_candidate():
    logger.info("Received request to /api/validate-candidate with headers: %s", request.headers)
    data = request.get_json()
    job_id = data.get('job_id')
    candidate_id = data.get('candidate_id')
    resume_url = data.get('resume_url')
    job_description = data.get('job_description')

    if not all([job_id, candidate_id, resume_url, job_description]):
        logger.error("Missing required fields: %s", data)
        return jsonify({"error": "Missing required fields"}), 400

    # Validate job_id exists in hr_jobs
    try:
        response = supabase.table("hr_jobs").select("id").eq("id", job_id).execute()
        if not response.data:
            logger.error("Job ID %s not found in hr_jobs", job_id)
            return jsonify({"error": "Job ID not found"}), 404
    except Exception as e:
        logger.error("Error validating job_id %s: %s", job_id, str(e))
        return jsonify({"error": "Failed to validate job ID"}), 500

    job = queue.enqueue(tasks.process_analysis, job_id, candidate_id, resume_url, job_description)
    logger.info("Enqueued job with ID: %s", job.id)
    return jsonify({"job_id": job.id}), 202

@app.route('/api/job-status/<job_id>', methods=['GET'])
def job_status(job_id):
    try:
        job = Job.fetch(job_id, connection=redis_conn)
        return jsonify({"status": job.get_status(), "result": job.result})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@app.route('/api/job-logs/<job_id>', methods=['GET'])
def job_logs(job_id):
    logs = redis_conn.lrange(f"job_logs:{job_id}", 0, -1)
    logs = [json.loads(log.decode('utf-8')) for log in logs]
    return jsonify({"logs": logs})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005)
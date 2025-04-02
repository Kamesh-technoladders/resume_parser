from flask import Flask, request, jsonify
from flask_cors import CORS
from rq.job import Job
import tasks
from config import queue, redis_conn

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

@app.route('/api/validate-candidate', methods=['POST'])
def validate_candidate():
    data = request.get_json()
    job_id = data.get('job_id')
    candidate_id = data.get('candidate_id')
    resume_url = data.get('resume_url')
    job_description = data.get('job_description')

    if not all([job_id, candidate_id, resume_url, job_description]):
        return jsonify({"error": "Missing required fields"}), 400

    job = queue.enqueue(tasks.process_analysis, job_id, candidate_id, resume_url, job_description)
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
# First, let's organize the backend with clear modules

# 1. Create a jobs.py file to handle all background jobs logic
# jobs.py
import json
import os
import logging
from datetime import datetime
import threading
from pathlib import Path
import time

logger = logging.getLogger(__name__)

# Configuration
JOBS_DIR = os.path.join(os.getcwd(), "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

class JobManager:
    @staticmethod
    def get_job_path(job_id):
        """Get the path to a job's status file"""
        return os.path.join(JOBS_DIR, f"{job_id}.json")
    
    @staticmethod
    def create_job_id(prefix):
        """Generate a unique job ID"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{prefix}_{timestamp}_{int(time.time() * 1000) % 10000}"
    
    @staticmethod
    def save_job_status(job_id, status_data):
        """Save job status to a file"""
        with open(JobManager.get_job_path(job_id), 'w') as f:
            json.dump(status_data, f)
        logger.info(f"Updated job status for {job_id}: {status_data['status']}")
    
    @staticmethod
    def get_job_status(job_id):
        """Get the current status for a job ID"""
        job_path = JobManager.get_job_path(job_id)
        if os.path.exists(job_path):
            try:
                with open(job_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error reading job status: {e}")
                return {'status': 'error', 'message': f'Could not read job status: {str(e)}'}
        return {'status': 'not_found', 'message': 'No status found for this job'}
    
    @staticmethod
    def start_job(job_type, target_func, args):
        """Start a background job and return its ID"""
        job_id = JobManager.create_job_id(job_type)
        
        # Initial status
        JobManager.save_job_status(job_id, {
            'status': 'processing',
            'progress': 0,
            'message': f'Starting {job_type} job...',
            'completed': False,
            'start_time': datetime.now().isoformat()
        })
        
        # Start the background thread
        thread = threading.Thread(
            target=target_func,
            args=args + (job_id,)  # Add job_id as the last argument
        )
        thread.daemon = True
        thread.start()
        
        return job_id
    
    @staticmethod
    def clean_old_jobs(max_age_hours=24):
        """Clean up old job files"""
        now = datetime.now()
        for job_file in Path(JOBS_DIR).glob("*.json"):
            try:
                # Get the file's modification time
                mtime = datetime.fromtimestamp(os.path.getmtime(job_file))
                age_hours = (now - mtime).total_seconds() / 3600
                
                # If the file is older than the max age, delete it
                if age_hours > max_age_hours:
                    os.remove(job_file)
                    logger.info(f"Removed old job file: {job_file}")
            except Exception as e:
                logger.error(f"Error cleaning up job file {job_file}: {e}")
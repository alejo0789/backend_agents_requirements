import json
import os
import logging
from datetime import datetime
import threading
import time
import tempfile

logger = logging.getLogger(__name__)

# Configuration
# Try to use a temp directory if filesystem is read-only
try:
    # First attempt to use the regular jobs directory
    JOBS_DIR = os.path.join(os.getcwd(), "jobs")
    os.makedirs(JOBS_DIR, exist_ok=True)
    # Test if we can write to this directory
    test_file = os.path.join(JOBS_DIR, "test_write.txt")
    with open(test_file, 'w') as f:
        f.write("test")
    os.remove(test_file)
    USE_FILESYSTEM = True
    logger.info(f"Using filesystem storage for jobs in {JOBS_DIR}")
except (OSError, IOError) as e:
    # If we get a permission error or any other file-related error,
    # fall back to in-memory storage
    logger.warning(f"Cannot write to filesystem: {e}. Using in-memory storage for jobs.")
    USE_FILESYSTEM = False

# In-memory storage as fallback
JOB_STORAGE = {}

class JobManager:
    @staticmethod
    def get_job_path(job_id):
        """Get the path to a job's status file"""
        if USE_FILESYSTEM:
            return os.path.join(JOBS_DIR, f"{job_id}.json")
        return job_id  # Just return the ID for in-memory storage
    
    @staticmethod
    def create_job_id(prefix):
        """Generate a unique job ID"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{prefix}_{timestamp}_{int(time.time() * 1000) % 10000}"
    
    @staticmethod
    def save_job_status(job_id, status_data):
        """Save job status to either a file or in-memory storage"""
        if USE_FILESYSTEM:
            try:
                with open(JobManager.get_job_path(job_id), 'w') as f:
                    json.dump(status_data, f)
            except (OSError, IOError) as e:
                # If filesystem write fails, fall back to in-memory
                logger.warning(f"Failed to write job status to file: {e}. Using in-memory storage.")
                JOB_STORAGE[job_id] = status_data
        else:
            # Use in-memory storage
            JOB_STORAGE[job_id] = status_data
        
        logger.info(f"Updated job status for {job_id}: {status_data['status']}")
    
    @staticmethod
    def get_job_status(job_id):
        """Get the current status for a job ID"""
        if USE_FILESYSTEM:
            job_path = JobManager.get_job_path(job_id)
            if os.path.exists(job_path):
                try:
                    with open(job_path, 'r') as f:
                        return json.load(f)
                except Exception as e:
                    logger.error(f"Error reading job status from file: {e}")
                    # Check if we have it in memory as fallback
                    if job_id in JOB_STORAGE:
                        return JOB_STORAGE[job_id]
                    return {'status': 'error', 'message': f'Could not read job status: {str(e)}'}
        else:
            # Use in-memory storage
            if job_id in JOB_STORAGE:
                return JOB_STORAGE[job_id]
        
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
        """Clean up old job files and/or memory storage"""
        now = datetime.now()
        
        # Clean memory storage
        jobs_to_remove = []
        for job_id, job_data in JOB_STORAGE.items():
            if 'start_time' in job_data:
                try:
                    start_time = datetime.fromisoformat(job_data['start_time'])
                    age_hours = (now - start_time).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        jobs_to_remove.append(job_id)
                except Exception as e:
                    logger.error(f"Error cleaning up in-memory job {job_id}: {e}")
        
        # Remove expired jobs from memory
        for job_id in jobs_to_remove:
            JOB_STORAGE.pop(job_id, None)
            logger.info(f"Removed old in-memory job: {job_id}")
        
        # Clean filesystem storage if used
        if USE_FILESYSTEM:
            try:
                import glob
                from pathlib import Path
                
                for job_file in glob.glob(os.path.join(JOBS_DIR, "*.json")):
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
            except Exception as e:
                logger.error(f"Error cleaning up job files: {e}")
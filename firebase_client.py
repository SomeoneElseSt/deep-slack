# firebase_client.py
import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from firebase_functions import scheduler_fn
from google.cloud import firestore
from croniter import croniter, CroniterBadCronError
import pytz

from openai_client import create_research_client

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT", "deep-slack")
REGION = "us-central1"

class FirebaseClient:
    def __init__(self):
        self.db = firestore.Client(project=PROJECT_ID)
        self.research_client = create_research_client()
    
    def get_firestore(self):
        """Get Firestore database instance"""
        return self.db
    
    def save_schedule(self, workspace_id: str, user_id: str, channel_id: str, 
                     prompt: str, cron_schedule: str, timezone_str: str = "UTC") -> str:
        """
        Save a new research schedule to Firestore
        Called when user creates a schedule via Slack
        """
        try:
            # Validate cron schedule
            if not self.validate_cron_schedule(cron_schedule):
                raise ValueError(f"Invalid cron schedule: {cron_schedule}")
            
            # Create schedule document
            schedule_data = {
                "workspace_id": workspace_id,
                "user_id": user_id,
                "channel_id": channel_id,
                "prompt": prompt,
                "cron_schedule": cron_schedule,
                "timezone": timezone_str,
                "created_at": firestore.SERVER_TIMESTAMP,
                "last_run": None,
                "active": True
            }
            
            # Save to Firestore
            doc_ref = self.db.collection("schedules").add(schedule_data)
            schedule_id = doc_ref[1].id
            
            logger.info(f"Schedule saved with ID: {schedule_id} for user {user_id}")
            return schedule_id
            
        except Exception as e:
            logger.error(f"Failed to save schedule: {e}")
            raise
    
    def get_active_schedules(self) -> List[Dict]:
        """Get all active schedules from Firestore"""
        try:
            schedules = []
            docs = self.db.collection("schedules").where("active", "==", True).stream()
            
            for doc in docs:
                schedule_data = doc.to_dict()
                schedule_data["id"] = doc.id
                schedules.append(schedule_data)
            
            logger.info(f"Retrieved {len(schedules)} active schedules")
            return schedules
            
        except Exception as e:
            logger.error(f"Failed to get active schedules: {e}")
            return []
    
    def update_last_run(self, schedule_id: str, timestamp: datetime):
        """Update the last run timestamp for a schedule"""
        try:
            self.db.collection("schedules").document(schedule_id).update({
                "last_run": timestamp
            })
            logger.info(f"Updated last_run for schedule {schedule_id}")
            
        except Exception as e:
            logger.error(f"Failed to update last_run: {e}")
    
    def validate_cron_schedule(self, cron_schedule: str) -> bool:
        """Validate cron schedule format"""
        try:
            croniter(cron_schedule)
            return True
        except CroniterBadCronError:
            return False
    
    def is_schedule_due(self, schedule: Dict) -> bool:
        """
        Check if a schedule is due to run
        Core logic for determining when jobs should execute
        """
        try:
            cron_schedule = schedule["cron_schedule"]
            timezone_str = schedule.get("timezone", "UTC")
            last_run = schedule.get("last_run")
            
            # Get current time in schedule's timezone
            tz = pytz.timezone(timezone_str)
            now = datetime.now(tz)
            
            # Create croniter instance
            cron = croniter(cron_schedule, now)
            
            # Get the previous scheduled time
            prev_time = cron.get_prev(datetime)
            
            # If never run, it's due
            if last_run is None:
                logger.info(f"Schedule {schedule['id']} never run - marking as due")
                return True
                
            # Convert last_run to timezone-aware datetime
            if hasattr(last_run, 'timestamp'):
                last_run_dt = datetime.fromtimestamp(last_run.timestamp(), tz=tz)
            else:
                last_run_dt = last_run.replace(tzinfo=tz)
            
            # Check if we should run (last run was before the previous scheduled time)
            should_run = last_run_dt < prev_time
            
            if should_run:
                logger.info(f"Schedule {schedule['id']} is due. Last run: {last_run_dt}, Previous scheduled: {prev_time}")
            
            return should_run
            
        except Exception as e:
            logger.error(f"Error checking if schedule is due: {e}")
            return False
    
    def add_to_outbox(self, workspace_id: str, channel_id: str, message: str):
        """Add a message to the outbox for Slack delivery"""
        try:
            outbox_data = {
                "workspace_id": workspace_id,
                "channel_id": channel_id,
                "message": message,
                "created_at": firestore.SERVER_TIMESTAMP,
                "delivered": False
            }
            
            self.db.collection("outbox").add(outbox_data)
            logger.info(f"Added message to outbox for channel {channel_id}")
            
        except Exception as e:
            logger.error(f"Failed to add to outbox: {e}")
    
    def process_due_schedules(self):
        """
        Main function: Process all schedules that are due to run
        This is called by the scheduled Cloud Function
        """
        try:
            schedules = self.get_active_schedules()
            processed_count = 0
            
            for schedule in schedules:
                if self.is_schedule_due(schedule):
                    self.execute_research_job(schedule)
                    processed_count += 1
            
            logger.info(f"Processed {processed_count} due schedules out of {len(schedules)} total")
            return processed_count
            
        except Exception as e:
            logger.error(f"Error processing due schedules: {e}")
            return 0
    
    def execute_research_job(self, schedule: Dict):
        """
        Execute a research job for a specific schedule
        Core business logic: prompt → OpenAI research → format → outbox
        """
        try:
            schedule_id = schedule["id"]
            prompt = schedule["prompt"]
            workspace_id = schedule["workspace_id"]
            channel_id = schedule["channel_id"]
            
            logger.info(f"Executing research job for schedule {schedule_id} with prompt: {prompt[:100]}...")
            
            # Validate prompt
            if not self.research_client.validate_prompt(prompt):
                logger.error(f"Invalid prompt for schedule {schedule_id}: {prompt}")
                return
            
            # Perform OpenAI research
            research_result = self.research_client.deep_research(prompt)
            
            # Format for Slack
            formatted_message = self.research_client.format_for_slack(research_result)
            
            # Add to outbox for Slack delivery
            self.add_to_outbox(workspace_id, channel_id, formatted_message)
            
            # Update last run timestamp
            self.update_last_run(schedule_id, datetime.now(timezone.utc))
            
            logger.info(f"Research job completed successfully for schedule {schedule_id}")
            
        except Exception as e:
            logger.error(f"Failed to execute research job for schedule {schedule.get('id', 'unknown')}: {e}")
            # Don't update last_run on failure so it retries
    
    def get_undelivered_messages(self) -> List[Dict]:
        """Get all undelivered messages from outbox for Slack client"""
        try:
            messages = []
            docs = self.db.collection("outbox").where("delivered", "==", False).order_by("created_at").stream()
            
            for doc in docs:
                message_data = doc.to_dict()
                message_data["id"] = doc.id
                messages.append(message_data)
            
            logger.info(f"Retrieved {len(messages)} undelivered messages")
            return messages
            
        except Exception as e:
            logger.error(f"Failed to get undelivered messages: {e}")
            return []
    
    def mark_message_delivered(self, message_id: str):
        """Mark a message as delivered (called by Slack client)"""
        try:
            self.db.collection("outbox").document(message_id).update({
                "delivered": True
            })
            logger.info(f"Marked message {message_id} as delivered")
            
        except Exception as e:
            logger.error(f"Failed to mark message as delivered: {e}")
    
    def deactivate_schedule(self, schedule_id: str):
        """Deactivate a schedule (user cancellation)"""
        try:
            self.db.collection("schedules").document(schedule_id).update({
                "active": False
            })
            logger.info(f"Deactivated schedule {schedule_id}")
            
        except Exception as e:
            logger.error(f"Failed to deactivate schedule: {e}")
    
    def get_user_schedules(self, workspace_id: str, user_id: str) -> List[Dict]:
        """Get all schedules for a specific user"""
        try:
            schedules = []
            docs = (self.db.collection("schedules")
                   .where("workspace_id", "==", workspace_id)
                   .where("user_id", "==", user_id)
                   .where("active", "==", True)
                   .stream())
            
            for doc in docs:
                schedule_data = doc.to_dict()
                schedule_data["id"] = doc.id
                schedules.append(schedule_data)
            
            logger.info(f"Retrieved {len(schedules)} schedules for user {user_id}")
            return schedules
            
        except Exception as e:
            logger.error(f"Failed to get user schedules: {e}")
            return []
    
    def get_schedule_by_id(self, schedule_id: str) -> Optional[Dict]:
        """Get a specific schedule by ID"""
        try:
            doc = self.db.collection("schedules").document(schedule_id).get()
            if doc.exists:
                schedule_data = doc.to_dict()
                schedule_data["id"] = doc.id
                return schedule_data
            return None
            
        except Exception as e:
            logger.error(f"Failed to get schedule by ID: {e}")
            return None

# Initialize Firebase client
firebase_client = FirebaseClient()

# ========== SCHEDULED CLOUD FUNCTION ==========
@scheduler_fn.on_schedule(schedule="*/15 * * * *", region=REGION)
def process_research_schedules(event):
    """
    Scheduled function that runs every 15 minutes to process due research jobs
    This is the heart of the system - continuously checking for due jobs
    """
    logger.info("Starting scheduled research job processing")
    
    try:
        processed_count = firebase_client.process_due_schedules()
        logger.info(f"Scheduled processing completed. Processed {processed_count} jobs.")
        
    except Exception as e:
        logger.error(f"Scheduled processing failed: {e}")

# ========== HELPER FUNCTIONS FOR SLACK CLIENT ==========
def save_user_schedule(workspace_id: str, user_id: str, channel_id: str, 
                      prompt: str, cron_schedule: str, timezone_str: str = "UTC") -> str:
    """Helper function to save a user schedule from Slack"""
    return firebase_client.save_schedule(workspace_id, user_id, channel_id, prompt, cron_schedule, timezone_str)

def get_outbox_messages() -> List[Dict]:
    """Helper function to get undelivered messages for Slack delivery"""
    return firebase_client.get_undelivered_messages()

def mark_delivered(message_id: str):
    """Helper function to mark message as delivered"""
    firebase_client.mark_message_delivered(message_id)

def get_user_schedules(workspace_id: str, user_id: str) -> List[Dict]:
    """Helper function to get user schedules"""
    return firebase_client.get_user_schedules(workspace_id, user_id)

def deactivate_user_schedule(schedule_id: str):
    """Helper function to deactivate a schedule"""
    firebase_client.deactivate_schedule(schedule_id)

def validate_cron_schedule(cron_schedule: str) -> bool:
    """Helper function to validate cron schedule"""
    return firebase_client.validate_cron_schedule(cron_schedule)

def get_schedule_by_id(schedule_id: str) -> Optional[Dict]:
    """Helper function to get schedule by ID"""
    return firebase_client.get_schedule_by_id(schedule_id)

# main.py - Firebase Functions for Deep Slack Research Bot
import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from firebase_functions import scheduler_fn
from google.cloud import firestore
from google.cloud.secretmanager import SecretManagerServiceClient 
from croniter import croniter, CroniterBadCronError
import pytz
import traceback


# OpenAI and other imports
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import weave

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT", "deep-slack")
REGION = "us-central1"

class OpenAIResearchClient:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.client = None
        self._initialize_client()
        self._initialize_weave()
    
    def _initialize_weave(self):
        """Initialize Weave tracking with W&B authentication"""
        try:
            # Get Wandb API key from Secret Manager
            wandb_key = self._get_secret("wandb-api-key")
            
            # Login to Wandb programmatically
            import wandb
            wandb.login(key=wandb_key)
            
            # Initialize Weave
            weave.init(project_name="deep-slack-research")
            logger.info("Weave tracking initialized successfully")
        except Exception as e:
            logger.warning(f"Weave initialization failed: {e}")
    
    def _get_secret(self, secret_name: str) -> str:
        """Retrieve secret from Google Secret Manager"""
        client = SecretManagerServiceClient()
        name = f"projects/{self.project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    
    def _initialize_client(self):
        """Initialize OpenAI client with API key from Secret Manager"""
        try:
            api_key = self._get_secret("openai-key")
            self.client = OpenAI(api_key=api_key)
            logger.info("OpenAI client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            raise
    
    @weave.op()
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((Exception,))
    )
    def deep_research(self, prompt: str) -> str:
        try:
            response = self.client.responses.create(
                model="o3-deep-research",  # Correct model
                input=prompt,              # Correct parameter
                tools=[
                    {"type": "web_search_preview"}  # Required tool
                ],
                max_output_tokens=25000    # Minimum recommended
            )
            
            return response.output_text  # Correct response access
            
        except Exception as e:
            logger.error(f"Deep research failed: {e}")
            raise
    
    def format_for_slack(self, content: str) -> str:
        """Format research content for Slack display"""
        # Fixed: Proper Slack formatting that preserves bold
        formatted = content.replace("### ", "*").replace("## ", "*").replace("# ", "*")
        formatted = formatted.replace("**", "*")  # Convert markdown bold to Slack bold
        # ← REMOVED: formatted.replace("*", "_") - this was breaking everything
        
        return f"🔬 *Deep Research Results* 🔬\n\n{formatted}"
    
    def validate_prompt(self, prompt: str) -> bool:
        """Validate research prompt for safety and quality"""
        if not prompt or len(prompt.strip()) < 10:
            return False
            
        forbidden_keywords = ["hack", "illegal", "harmful"]
        if any(keyword in prompt.lower() for keyword in forbidden_keywords):
            return False
            
        return True


class FirebaseClient:
    def __init__(self):
        try:
            logging.info("Starting Firebase client initialization...")
            logging.info(f"FIREBASE_CONFIG present: {'FIREBASE_CONFIG' in os.environ}")
            logging.info(f"GOOGLE_APPLICATION_CREDENTIALS present: {'GOOGLE_APPLICATION_CREDENTIALS' in os.environ}")
            self.db = firestore.Client(project=PROJECT_ID)
            self.research_client = OpenAIResearchClient(PROJECT_ID)
        except Exception as e:
            logging.error(f"Firebase initialization failed: {str(e)}")
            logging.error(f"Stack trace: {traceback.format_exc()}")
            raise
    
    def get_firestore(self):
        """Get Firestore database instance"""
        return self.db
    
    def save_schedule(self, workspace_id: str, user_id: str, channel_id: str, 
                     prompt: str, cron_schedule: str, timezone_str: str = "UTC") -> str:
        """Save a new research schedule to Firestore"""
        try:
            if not self.validate_cron_schedule(cron_schedule):
                raise ValueError(f"Invalid cron schedule: {cron_schedule}")
            
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
        """Check if a schedule is due to run using previous-occurrence logic"""
        try:
            cron_schedule = schedule["cron_schedule"]
            timezone_str = schedule.get("timezone", "UTC")
            last_run = schedule.get("last_run")
            
            tz = pytz.timezone(timezone_str)
            now = datetime.now(tz)
            
            cron = croniter(cron_schedule, now)
            
            # Get PREVIOUS occurrence (when it should have run)
            prev_time = cron.get_prev(datetime)
            
            # Check if we're past the scheduled time and haven't run yet
            if now >= prev_time:
                # If never run, or last run was before this scheduled time
                if last_run is None or last_run < prev_time:
                    logger.info(f"Schedule {schedule['id']} is due - scheduled: {prev_time}, last_run: {last_run}")
                    return True
            
            return False
            
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
        """Process all schedules that are due to run"""
        try:
            schedules = self.get_active_schedules()
            logger.info(f"🔍 Checking {len(schedules)} active schedules")
            
            processed_count = 0
            for schedule in schedules:
                schedule_id = schedule["id"]
                cron_schedule = schedule["cron_schedule"]
                
                logger.info(f"📅 Checking schedule {schedule_id}: {cron_schedule}")
                
                if self.is_schedule_due(schedule):
                    logger.info(f"🔥 Executing due schedule: {schedule_id}")
                    self.execute_research_job(schedule)
                    processed_count += 1
                else:
                    logger.info(f"⏭️ Schedule {schedule_id} not due yet")
            
            logger.info(f"✅ Processed {processed_count} due schedules out of {len(schedules)} total")
            return processed_count
            
        except Exception as e:
            logger.error(f"❌ Error processing due schedules: {e}")
            return 0

    
    def execute_research_job(self, schedule: Dict):
        """Execute a research job for a specific schedule"""
        try:
            schedule_id = schedule["id"]
            prompt = schedule["prompt"]
            workspace_id = schedule["workspace_id"]
            channel_id = schedule["channel_id"]
            
            logger.info(f"Executing research job for schedule {schedule_id} with prompt: {prompt[:100]}...")
            
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
        """Mark a message as delivered"""
        try:
            self.db.collection("outbox").document(message_id).update({
                "delivered": True
            })
            logger.info(f"Marked message {message_id} as delivered")
        except Exception as e:
            logger.error(f"Failed to mark message as delivered: {e}")
    
    def deactivate_schedule(self, schedule_id: str):
        """Deactivate a schedule"""
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

try:
    logger.info("Attempting to initialize Firebase client...")
    logger.info(f"Project ID: {PROJECT_ID}")
    
    # Test Firestore connection first
    test_db = firestore.Client(project=PROJECT_ID)
    logger.info("Firestore client test successful")
    
    # Initialize Firebase client
    firebase_client = FirebaseClient()
    logger.info("Firebase client initialized successfully")
    
except Exception as e:
    logger.error(f"Failed to initialize Firebase client: {e}")
    logger.error(f"Error type: {type(e)}")
    logger.error(f"Error details: {str(e)}")
    import traceback
    logger.error(f"Traceback: {traceback.format_exc()}")
    firebase_client = None

print(f"🔍 DEBUG: firebase_client = {firebase_client}")
print(f"🔍 DEBUG: firebase_client type = {type(firebase_client)}")

if firebase_client is None:
    print("❌ Firebase client failed to initialize!")
else:
    print("✅ Firebase client initialized successfully")

# ========== SCHEDULED CLOUD FUNCTION ==========
@scheduler_fn.on_schedule(
    schedule="*/5 * * * *",
    region=REGION,
    timeout_sec=1000,
    memory=1024,
    min_instances=1,
    max_instances=1,
)
def process_research_schedules(event):
    """
    Scheduled function that runs every 5 minutes to process due research jobs
    """
    logger.info("Starting scheduled research job processing")
    
    try:
        if firebase_client is None:
            logger.error("Firebase client not initialized - skipping execution")
            return "Firebase client initialization failed"
            
        processed_count = firebase_client.process_due_schedules()
        logger.info(f"Scheduled processing completed. Processed {processed_count} jobs.")
        return f"Processed {processed_count} jobs"
        
    except Exception as e:
        logger.error(f"Scheduled processing failed: {e}")
        return f"Error: {str(e)}"

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

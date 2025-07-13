import logging
from deep_slack.main import FirebaseClient
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def force_execute_schedule(schedule_id: str):
    """Force execute a specific schedule for testing"""
    client = FirebaseClient()
    try:
        schedule = client.get_schedule_by_id(schedule_id)
        if schedule:
            logger.info(f"🔥 Force executing schedule: {schedule_id}")
            client.execute_research_job(schedule)
        else:
            logger.error(f"❌ Schedule not found: {schedule_id}")
    except Exception as e:
        logger.error(f"❌ Force execution failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_firebase.py <schedule_id>")
        sys.exit(1)
    schedule_id = sys.argv[1]
    force_execute_schedule(schedule_id)

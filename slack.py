# slack.py
import os
import re
import json
import logging
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)

logger = logging.getLogger(__name__)

from deep_slack.main import (
    save_user_schedule, 
    get_user_schedules, 
    deactivate_user_schedule,
    validate_cron_schedule,
    get_schedule_by_id,
    get_outbox_messages,
    mark_delivered
)

load_dotenv()

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

# In-memory conversation state (for hackathon - use Redis/Firestore for production)
user_sessions = {}

class SetupSession:
    def __init__(self, user_id, channel_id, workspace_id):
        self.user_id = user_id
        self.channel_id = channel_id
        self.workspace_id = workspace_id
        self.prompt = None
        self.cron_schedule = None
        self.friendly_schedule = None
        self.step = "waiting_for_prompt"  # waiting_for_prompt, waiting_for_schedule, confirming
        self.editing_mode = False
        self.existing_schedule_id = None

# ========== CONVERSATION STATE MANAGEMENT ==========

def get_session(user_id):
    return user_sessions.get(user_id)

def start_session(user_id, channel_id, workspace_id):
    session = SetupSession(user_id, channel_id, workspace_id)
    user_sessions[user_id] = session
    return session

def end_session(user_id):
    if user_id in user_sessions:
        del user_sessions[user_id]

# ========== CRON PARSING UTILITIES ==========
def parse_friendly_schedule(schedule_text):
    """
    Enhanced parser supporting minutes
    Examples:
    - "Monday 9" → "0 9 * * 1"
    - "Monday 9:30" → "30 9 * * 1"
    - "Every day 8:15" → "15 8 * * *"
    - "Weekdays 14:45" → "45 14 * * 1-5"
    """
    schedule_text = schedule_text.lower().strip()
    
    # Day mappings (unchanged)
    day_map = {
        'monday': '1', 'tuesday': '2', 'wednesday': '3', 'thursday': '4',
        'friday': '5', 'saturday': '6', 'sunday': '0',
        'mon': '1', 'tue': '2', 'wed': '3', 'thu': '4',
        'fri': '5', 'sat': '6', 'sun': '0'
    }
    
    # Enhanced time parsing with minutes support
    # Matches: "9", "9:30", "14:45", "8:15", etc.
    time_match = re.search(r'(\d{1,2})(?::(\d{2}))?(?:\s*(?:am|pm|h|hr|hour)?)?$', schedule_text)
    if not time_match:
        return None, "Please specify a time (e.g., '9', '9:30', '14:45')"
    
    hour = int(time_match.group(1))
    minute = int(time_match.group(2)) if time_match.group(2) else 0
    
    # Validation
    if hour < 0 or hour > 23:
        return None, "Hour must be between 0 and 23 (24-hour format)"
    if minute < 0 or minute > 59:
        return None, "Minutes must be between 0 and 59"
    
    # Remove time from text to analyze days
    days_text = schedule_text[:time_match.start()].strip()
    
    # Handle special cases (unchanged logic)
    if 'every day' in days_text or 'daily' in days_text:
        friendly_time = f"{hour}:{minute:02d}" if minute > 0 else f"{hour}:00"
        return f"{minute} {hour} * * *", f"Every day at {friendly_time}"
    
    if 'weekdays' in days_text or 'weekday' in days_text:
        friendly_time = f"{hour}:{minute:02d}" if minute > 0 else f"{hour}:00"
        return f"{minute} {hour} * * 1-5", f"Weekdays at {friendly_time}"
    
    if 'weekend' in days_text:
        friendly_time = f"{hour}:{minute:02d}" if minute > 0 else f"{hour}:00"
        return f"{minute} {hour} * * 6,0", f"Weekends at {friendly_time}"
    
    # Parse specific days
    days = []
    for day_name, day_num in day_map.items():
        if day_name in days_text:
            days.append(day_num)
    
    if not days:
        return None, "Please specify day(s) of the week"
    
    # Remove duplicates and sort
    days = sorted(list(set(days)))
    day_string = ','.join(days)
    
    # Create friendly description with minutes
    day_names = []
    reverse_day_map = {v: k for k, v in day_map.items() if len(k) > 3}
    for day in days:
        day_names.append(reverse_day_map.get(day, day).capitalize())
    
    friendly_days = ', '.join(day_names[:-1]) + (' and ' + day_names[-1] if len(day_names) > 1 else day_names[0])
    friendly_time = f"{hour}:{minute:02d}" if minute > 0 else f"{hour}:00"
    friendly_desc = f"{friendly_days} at {friendly_time}"
    
    cron = f"{minute} {hour} * * {day_string}"
    return cron, friendly_desc

def cron_to_friendly(cron: str) -> str:
    """Convert a cron string (minute hour * * day) to a human-friendly description."""
    # Only support the format we generate: 'minute hour * * day(s)'
    parts = cron.strip().split()
    if len(parts) != 5:
        return cron  # fallback
    minute, hour, _, _, days = parts
    # Day mapping
    day_map = {
        '0': 'Sunday', '1': 'Monday', '2': 'Tuesday', '3': 'Wednesday', '4': 'Thursday', '5': 'Friday', '6': 'Saturday'
    }
    # Handle special cases
    if days == '*':
        day_str = 'Every day'
    elif days == '1-5':
        day_str = 'Weekdays'
    elif days == '6,0' or days == '0,6':
        day_str = 'Weekends'
    else:
        day_nums = days.split(',')
        day_names = [day_map.get(d, d) for d in day_nums]
        if len(day_names) == 1:
            day_str = day_names[0]
        else:
            day_str = ', '.join(day_names[:-1]) + ' and ' + day_names[-1]
    # Format time
    try:
        hour_int = int(hour)
        minute_int = int(minute)
        time_str = f"{hour_int}:{minute_int:02d}"
    except Exception:
        time_str = f"{hour}:{minute}"
    return f"{day_str} at {time_str}"

# ========== MESSAGE HANDLERS ==========

@app.message("hi")
def handle_greeting(message, say):
    user_id = message['user']
    channel_id = message['channel']
    workspace_id = message.get('team')
    
    # Start new setup session
    session = start_session(user_id, channel_id, workspace_id)
    
    say(f"Hello <@{user_id}>! 👋\n\nI'm your Deep Research assistant. I can help you set up automated research reports.\n\n*Step 1️⃣:* Please provide your deep research prompt.\n\n*Example 🧠: \"Latest trends in artificial intelligence and machine learning\"*")

@app.event("message")
def handle_message_events(body, say, logger):
    event = body["event"]
    
    # Ignore bot messages
    if event.get("bot_id") or event.get("subtype"):
        return
    
    user_id = event.get("user")
    text = event.get("text", "").strip()
    channel_id = event.get("channel")
    workspace_id = body.get("team_id")
    
    # Handle conversation flow
    session = get_session(user_id)
    if session:
        handle_setup_conversation(session, text, say, logger)

def handle_setup_conversation(session, text, say, logger):
    if session.step == "waiting_for_prompt":
        # Step 1: Receive and confirm prompt
        session.prompt = text
        session.step = "confirming_prompt"
        
        say(f"✅ *Prompt received:*\n\n*\"{text}\"*\n\n*Step 2️⃣:* Now let's set up your schedule! When do you want to receive these research reports?\n\nPlease tell me:\n• *Day(s) of the week* (e.g., Monday, Wednesday, Friday)\n• *Hour of the day* in 24-hour format (e.g., 9 for 9 AM, 14 for 2 PM)\n\n*Examples:*\n• \"Monday 9\" (Mondays at 9 AM)\n• \"Every day 8\" (Daily at 8 AM)\n• \"Weekdays 14\" (Weekdays at 2 PM)\n• \"Monday Wednesday Friday 10\" (MWF at 10 AM)")
        
        session.step = "waiting_for_schedule"
    
    elif session.step == "waiting_for_schedule":
        # Step 2: Parse schedule
        cron, friendly_desc = parse_friendly_schedule(text)
        
        if not cron:
            say(f"❌ *Schedule format error:* {friendly_desc}\n\nPlease try again with format like:\n• \"Monday 9\" (Mondays at 9 AM)\n• \"Every day 8\" (Daily at 8 AM)\n• \"Weekdays 14\" (Weekdays at 2 PM)")
            return
        
        # Validate cron
        if not validate_cron_schedule(cron):
            say(f"❌ *Invalid schedule format.* Please try again.")
            return
        
        session.cron_schedule = cron
        session.friendly_schedule = friendly_desc
        session.step = "confirming"
        
        say(f"✅ *Schedule set:* {friendly_desc}\n\n*Final confirmation:*\n\n🔬 *Research Prompt:* {session.prompt}\n\n📅 *Schedule:* {friendly_desc}\n\nType *'confirm'* to save this schedule or *'cancel'* to start over.")
    
    elif session.step == "confirming":
        if text.lower() == "confirm":
            # Save to database
            try:
                schedule_id = save_user_schedule(
                    workspace_id=session.workspace_id,
                    user_id=session.user_id,
                    channel_id=session.channel_id,
                    prompt=session.prompt,
                    cron_schedule=session.cron_schedule,
                    timezone_str="UTC"  # Can be enhanced to detect user timezone
                )
                
                say(f"🎉 *Research schedule created successfully!*\n\n✅ Your reports will be delivered {session.friendly_schedule}\n✅ Schedule ID: `{schedule_id}`\n\nYou can manage your schedules with `/setup-deep-research`")
                
                end_session(session.user_id)
                
            except Exception as e:
                logger.error(f"Failed to save schedule: {e}")
                say(f"❌ *Error saving schedule:* {str(e)}\n\nPlease try again.")
        
        elif text.lower() == "cancel":
            say("❌ *Setup cancelled.* Type 'hi' to start over.")
            end_session(session.user_id)
        
        else:
            say("Please type *'confirm'* to save or *'cancel'* to start over.")

# ========== SLASH COMMANDS ==========

@app.command("/setup-deep-research")
def handle_setup_command(ack, body, client, logger):
    ack()
    
    user_id = body["user_id"]
    channel_id = body["channel_id"]
    workspace_id = body["team_id"]
    
    # Get existing schedules
    schedules = get_user_schedules(workspace_id, user_id)
    
    if not schedules:
        # No existing schedules - start fresh setup
        session = start_session(user_id, channel_id, workspace_id)
        client.chat_postMessage(
            channel=channel_id,
            text="Welcome to Deep Research setup! 🔬\n\n*Step 1️⃣:* Please provide your deep research prompt.\n\n*Example 🧠: \"Latest trends in artificial intelligence\"*"
        )
        return
    
    # Show existing schedules and options
    schedule_text = ""
    for i, schedule in enumerate(schedules[:3]):  # Show max 3
        friendly = cron_to_friendly(schedule['cron_schedule'])
        schedule_text += f"{i+1}. *\"{schedule['prompt'][:50]}...\"*\n   📅 Schedule: {friendly}\n   🆔 ID: `{schedule['id']}`\n\n"
    
    client.chat_postMessage(
        channel=channel_id,
        text=f"*Your current research schedules:*\n\n{schedule_text}*What would you like to do?*\n• Type *'new'* to create a new schedule\n• Type *'edit [schedule_id]'* to modify a schedule\n• Type *'delete [schedule_id]'* to remove a schedule\n• Type *'list'* to see all schedules"
    )

@app.command("/my-schedules")
def handle_my_schedules(ack, body, client):
    ack()
    
    user_id = body["user_id"]
    workspace_id = body["team_id"]
    channel_id = body["channel_id"]
    
    schedules = get_user_schedules(workspace_id, user_id)
    
    if not schedules:
        client.chat_postMessage(
            channel=channel_id,
            text="📋 *No active research schedules found.*\n\nType 'hi' to create your first schedule!"
        )
        return
    
    schedule_text = "📋 *Your Active Research Schedules:*\n\n"
    for i, schedule in enumerate(schedules):
        friendly = cron_to_friendly(schedule['cron_schedule'])
        schedule_text += f"*{i+1}.* *\"{schedule['prompt'][:100]}...\"*\n"
        schedule_text += f"   📅 Schedule: {friendly}\n"
        schedule_text += f"   🆔 ID: `{schedule['id']}`\n"
        schedule_text += f"   📊 Channel: <#{schedule['channel_id']}>\n\n"
    
    client.chat_postMessage(
        channel=channel_id,
        text=schedule_text
    )

# ========== MESSAGE DELIVERY SYSTEM ==========

def deliver_outbox_messages():
    """Deliver pending messages from Firebase outbox to Slack"""
    try:
        messages = get_outbox_messages()
        logger.info(f"📬 Found {len(messages)} undelivered messages")
        
        if not messages:
            logger.info("✅ No messages to deliver")
            return
        
        for message in messages:
            try:
                logger.info(f"📤 Delivering message {message['id']} to channel {message['channel_id']}")
                
                app.client.chat_postMessage(
                    channel=message["channel_id"],
                    text=message["message"]
                )
                
                # Mark as delivered
                mark_delivered(message["id"])
                logger.info(f"✅ Message {message['id']} delivered successfully")
                
            except Exception as e:
                logger.error(f"❌ Failed to deliver message {message['id']}: {e}")
                logger.error(f"❌ Channel: {message['channel_id']}")
                logger.error(f"❌ Message preview: {message['message'][:100]}...")
                
    except Exception as e:
        logger.error(f"❌ Error getting outbox messages: {e}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")

# ========== BACKGROUND TASKS ==========

import threading
import time

def background_message_delivery():
    """Background task to deliver outbox messages"""
    while True:
        try:
            deliver_outbox_messages()
            time.sleep(60)  # Check every minute
        except Exception as e:
            logger.error(f"Background delivery error: {e}")
            time.sleep(60)

# Start background delivery
delivery_thread = threading.Thread(target=background_message_delivery, daemon=True)
delivery_thread.start()

# ========== ERROR HANDLER ==========

@app.error
def error_handler(error, body, logger):
    logger.exception(f"Error: {error}")
    logger.info(f"Request body: {body}")

# ========== MAIN ==========

if __name__ == "__main__":
    print("⚡ Starting Deep Research Slack Bot...")
    print("✅ Message delivery system started")
    print("✅ Conversation handlers loaded")
    print("🚀 Starting Socket Mode connection...")
    
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()

def test_firebase_connection():
    """Test Firebase connection from Slack bot"""
    try:
        messages = get_outbox_messages()
        print(f"✅ Firebase connection working - found {len(messages)} messages")
        return True
    except Exception as e:
        print(f"❌ Firebase connection failed: {e}")
        return False

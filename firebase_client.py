# firebase_client.py
import os
from firebase_functions import scheduler_fn              # 2-gen Functions
from google.cloud import firestore, secretmanager
from croniter import croniter, CroniterBadCronError
import openai
from datetime import datetime, timezone
PROJECT_ID = os.getenv("GCP_PROJECT")  # auto-set inside Functions
REGION = "us-central1"

# ---------- helpers ----------
def get_secret(name: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    res = client.access_secret_version(
        request={"name": f"projects/{PROJECT_ID}/secrets/{name}/versions/latest"}
    )
    return res.payload.data.decode()

def get_firestore():
    return firestore.Client(project=PROJECT_ID)

def call_openai(prompt: str) -> str:
    openai.api_key = get_secret("openai-key")
    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        stream=False,
    )
    return resp.choices[0].message.content

# ---------- scheduled job ----------
@scheduler_fn.on_schedule(schedule="0 * * * *", region=REGION)  # top-of-hour default
def run_research_job(event):
    db = get_firestore()
    docs = db.collection("schedules").stream()

    for doc in docs:
        data = doc.to_dict()
        cron = data["cron"]
        prompt = data["prompt"]
        channel = data["slack_channel"]

        # validate cron once per run
        try:
            cron_iter = croniter(cron)
        except CroniterBadCronError:
            print(f"Bad cron {cron} for {doc.id}")
            continue

        # check if cron is due now
        now = datetime.now(timezone.utc)
        
        # Get the last execution time from Firestore (or use epoch if first run)
        last_run = data.get("last_run")
        if last_run:
            last_run_dt = last_run.replace(tzinfo=timezone.utc)
        else:
            last_run_dt = datetime.fromtimestamp(0, timezone.utc)
        
        # Check if there's a scheduled time between last run and now
        cron_iter.set_current(last_run_dt)
        next_run = cron_iter.get_next(datetime)
        
        if next_run > now:
            # Not due yet
            continue
        
        result = call_openai(prompt)
        
        # Update last run timestamp
        db.collection("schedules").document(doc.id).update({
            "last_run": firestore.SERVER_TIMESTAMP
        })

        # enqueue message via Firestore (picked up by Slack client)
        db.collection("outbox").add(
            {"channel": channel, "text": result, "ts": firestore.SERVER_TIMESTAMP}
        )

import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Initialize your app with your bot token and signing secret
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)

# Add a simple slash command handler
@app.command("/hello")
def hello_command(ack, respond, command):
    # Acknowledge command request
    ack()
    respond(f"Hey there <@{command['user_id']}>!")

# Add a message event handler
@app.event("message")
def handle_message_events(body, logger):
    logger.info(body)

# Add a reaction event handler
@app.event("reaction_added")
def handle_reaction_added_events(body, logger):
    logger.info(body)

# Start your app
if __name__ == "__main__":
    # For development, use Socket Mode
    if os.environ.get("SLACK_APP_TOKEN"):
        SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN")).start()
    else:
        # For production, use HTTP mode
        app.start(port=int(os.environ.get("PORT", 3000)))

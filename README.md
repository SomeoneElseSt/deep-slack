# Deep Slack Bot

A Slack bot built with Python using the Slack Bolt framework, designed for the Slack marketplace.

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create a Slack app:**
   - Go to https://api.slack.com/apps
   - Click "Create New App" -> "From scratch"
   - Give your app a name and select a workspace

3. **Configure your app:**
   - Go to "OAuth & Permissions" and add these Bot Token Scopes:
     - `chat:write`
     - `commands`
     - `reactions:read`
   - Install the app to your workspace

4. **Get your tokens:**
   - Bot User OAuth Token from "OAuth & Permissions"
   - Signing Secret from "Basic Information"
   - App-Level Token from "Basic Information" (for Socket Mode)

5. **Set up environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your actual tokens
   ```

6. **Enable Socket Mode (for development):**
   - Go to "Socket Mode" in your app settings
   - Enable Socket Mode and create an App-Level Token

## Running the Bot

### Development (Socket Mode)
```bash
python app.py
```

### Production (HTTP Mode)
Set up a public URL (using ngrok, Railway, Heroku, etc.) and configure your app's Request URL.

## Features

- `/hello` slash command
- Message event handling
- Reaction event handling

## Marketplace Distribution

To distribute on the Slack marketplace:

1. **App Directory Listing:**
   - Complete your app's metadata
   - Add app icon and descriptions
   - Set up proper OAuth scopes

2. **Review Process:**
   - Submit for Slack's review
   - Ensure compliance with Slack's guidelines
   - Test thoroughly across different workspaces

3. **Distribution:**
   - Public distribution via Slack App Directory
   - Or private distribution with install links

## Development

The bot is structured to be easily extensible. Add new commands and event handlers in `app.py`.

## License

MIT License

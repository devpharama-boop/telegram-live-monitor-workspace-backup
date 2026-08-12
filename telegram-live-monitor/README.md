# 🤖 Telegram Live Stream Monitor Bot

> Auto-monitors Telegram channels for live streams and sends DMs to viewers

## ✨ Features

- 🔴 **Live Stream Detection** — Auto-detects when channels go live
- ✉️ **Auto DM** — Sends configured message to all viewers
- 📺 **Multi-Channel** — Monitor unlimited channels
- 👤 **Multi-Account** — Login multiple Telegram accounts
- 💬 **Custom Messages** — Edit DM text with image support
- 🛡️ **Admin Panel** — Add/remove admins
- 🎨 **Dark Dashboard** — Beautiful web UI for full control

## 🚀 Quick Start

```bash
# 1. Clone & setup
git clone <repo-url>
cd telegram-live-monitor
bash setup.sh

# 2. Edit .env with your credentials
nano .env

# 3. Run
python web_dashboard.py
```

Open **http://localhost:5000**

## 📱 Bot Commands (in Telegram)

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/stats` | Bot statistics |
| `/channels` | List monitored channels |
| `/accounts` | List connected accounts |
| `/setmsg <text>` | Set DM message |
| `/resetdm` | Reset DM records |
| `/addchannel <link>` | Add channel |
| `/help` | Show help |

## 🗂️ Project Structure

```
telegram-live-monitor/
├── bot.py              # Core bot engine (Telethon)
├── web_dashboard.py    # Flask web dashboard
├── templates/
│   └── dashboard.html  # Web UI
├── requirements.txt    # Python dependencies
├── .env                # Environment variables
├── Procfile            # Railway deployment
└── local_db.json       # Local database (auto-generated)
```

## 🌐 Deploy on Railway

1. Push to GitHub
2. Connect Railway to your repo
3. Set env vars in Railway dashboard
4. Deploy!

## 🔐 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_API_ID` | ✅ | Telegram API ID |
| `TELEGRAM_API_HASH` | ✅ | Telegram API Hash |
| `BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `FIREBASE_DB_URL` | ❌ | Firebase Realtime DB URL |
| `FIREBASE_CRED_PATH` | ❌ | Firebase credentials JSON path |
| `SECRET_KEY` | ✅ | Flask secret key |
| `PORT` | ❌ | Web port (default 5000) |

## 📝 How It Works

1. **Add your Telegram account** via Account button
2. **Add channels** to monitor via Add Channel
3. **Set your DM message** via Set Message
4. **Bot auto-monitors** — when someone goes live, viewers get DMs
5. Each viewer gets DM **only once** per session
6. Use **Reset DM Records** to allow re-DM

## ⚠️ Important

- Bot must be **admin** in monitored channels
- Use **invite links** for private channels
- Delay between DMs to avoid Telegram flood limits
- Each user DMed only once until records reset

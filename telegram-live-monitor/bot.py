#!/usr/bin/env python3
"""
Telegram Live Stream Monitor Bot v3
Monitors channels for live streams and sends DMs to viewers from ALL connected accounts.
Built with Telethon + Firebase Realtime Database.
"""

import os
import json
import asyncio
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient, events, types, functions, errors
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageMediaPhoto, MessageMediaDocument,
    InputPeerChannel, InputPeerUser, PeerChannel, PeerUser
)
from telethon.tl.functions.messages import GetDialogsRequest, CheckChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest, GetParticipantRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
import firebase_admin
from firebase_admin import credentials, db

load_dotenv()

# ==================== CONFIG ====================
API_ID = int(os.getenv("TELEGRAM_API_ID", "35812449"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "099cfed535a5b2dcd8e43f157d30e3ce")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8710003468:AAFou6EOMDf0L7tr2cId3K2dwDbR-6AfQXM")
ADMIN_IDS = [5844447576]
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "")
FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "firebase-cred.json")

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== DATABASE ====================
firebase_ref = None
LOCAL_DB_PATH = Path("local_db.json")
local_db = {}

def load_local_db():
    global local_db
    if LOCAL_DB_PATH.exists():
        with open(LOCAL_DB_PATH, 'r') as f:
            local_db = json.load(f)

def save_local_db():
    with open(LOCAL_DB_PATH, 'w') as f:
        json.dump(local_db, f, indent=2, default=str)

def init_firebase():
    global firebase_ref
    try:
        if FIREBASE_DB_URL:
            cred = credentials.Certificate(FIREBASE_CRED_PATH)
            firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
            firebase_ref = db.reference('/')
            logger.info("Firebase connected")
    except Exception as e:
        logger.warning(f"Firebase init failed, using local DB: {e}")

def fb_get(path, default=None):
    try:
        if firebase_ref:
            val = firebase_ref.child(path).get()
            return val if val is not None else default
    except:
        pass
    parts = path.strip('/').split('/')
    d = local_db
    for part in parts:
        if isinstance(d, dict) and part in d:
            d = d[part]
        else:
            return default
    return d

def fb_set(path, value):
    if firebase_ref:
        try:
            firebase_ref.child(path).set(value)
        except Exception as e:
            logger.warning(f"Firebase write failed for {path}: {e}")
    # Always write to local DB as fallback
    parts = path.strip('/').split('/')
    d = local_db
    for part in parts[:-1]:
        if part not in d:
            d[part] = {}
        d = d[part]
    d[parts[-1]] = value
    save_local_db()
    # Sync to Firebase if available
    if firebase_ref:
        try:
            firebase_ref.child(path).set(value)
        except:
            pass

def fb_update(path, value):
    current = fb_get(path, {}) or {}
    if isinstance(current, dict):
        current.update(value)
        fb_set(path, current)

def fb_delete(path):
    if firebase_ref:
        try:
            firebase_ref.child(path).delete()
        except:
            pass
    parts = path.strip('/').split('/')
    d = local_db
    for part in parts[:-1]:
        if part in d:
            d = d[part]
        else:
            return
    if parts[-1] in d:
        del d[parts[-1]]
    save_local_db()


# ==================== CLIENT POOL ====================
class ClientPool:
    """Manages multiple Telegram client sessions stored in Firebase."""

    def __init__(self):
        self.clients: dict = {}  # user_id -> TelegramClient
        self.me = None
        self.session_strings: dict = {}  # user_id -> session_string

    def load_sessions_from_db(self):
        """Load session strings from Firebase/local DB."""
        sessions = fb_get('sessions', {}) or {}
        self.session_strings = sessions
        logger.info(f"Loaded {len(sessions)} session strings from DB")

    def save_session_to_db(self, user_id: int, session_string: str):
        """Save session string to DB for persistence."""
        self.session_strings[str(user_id)] = session_string
        fb_set(f'sessions/{user_id}', session_string)

    def get_session_string(self, user_id: int) -> str:
        """Get stored session string."""
        return self.session_strings.get(str(user_id), '')

    async def init_main(self):
        """Initialize main admin client from stored session string."""
        self.load_sessions_from_db()
        
        # PRIMARY: Hardcoded session string for reliability
        HARDCODED_SESSION = os.getenv("MAIN_SESSION_STRING", "")
        ADMIN_ID = 5844447576
        
        if HARDCODED_SESSION:
            from telethon.sessions import StringSession
            self.main_client = TelegramClient(StringSession(HARDCODED_SESSION), API_ID, API_HASH)
            await self.main_client.connect()
            if await self.main_client.is_user_authorized():
                self.me = await self.main_client.get_me()
                self.clients[self.me.id] = self.main_client
                logger.info(f"✅ Main client from hardcoded session: {self.me.first_name} (ID={self.me.id})")
                return
            else:
                logger.warning("⚠️ Hardcoded session expired!")
        
        # FALLBACK: Try DB session string
        main_session = self.get_session_string(ADMIN_ID)
        if main_session:
            from telethon.sessions import StringSession
            self.main_client = TelegramClient(StringSession(main_session), API_ID, API_HASH)
            await self.main_client.connect()
            if await self.main_client.is_user_authorized():
                self.me = await self.main_client.get_me()
                self.clients[self.me.id] = self.main_client
                logger.info(f"✅ Main client from DB session: {self.me.first_name} (ID={self.me.id})")
                return
        
        # LAST RESORT: NO session available - log critical and raise
        logger.critical("❌ NO SESSION! Add MAIN_SESSION_STRING env var or login via Dashboard.")
        logger.critical("⚠️ Bot monitoring will be DISABLED. Dashboard is still accessible.")
        raise RuntimeError("No Telegram session available. Login via Dashboard first.")

    async def load_all_accounts(self):
        """Load all saved accounts from DB."""
        self.load_sessions_from_db()
        accounts = fb_get('accounts', {}) or {}
        
        if not accounts:
            logger.warning("⚠️ ZERO accounts in DB! No monitoring possible.")
            return

        for user_id_str, acc in accounts.items():
            try:
                user_id = int(user_id_str)
                if user_id in self.clients:
                    continue
                
                # Try session string first (persisted across redeploys)
                session_str = self.get_session_string(user_id)
                
                if session_str:
                    from telethon.sessions import StringSession
                    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
                    await client.connect()
                    if not await client.is_user_authorized():
                        logger.warning(f"Session expired for account {user_id}, need re-login")
                        continue
                else:
                    # Fallback: try local session file
                    session_name = acc.get('session_name', f'account_{user_id}')
                    client = TelegramClient(session_name, API_ID, API_HASH)
                    await client.start()
                    # Save session string for persistence
                    session_str = client.session.save()
                    self.save_session_to_db(user_id, session_str)

                self.clients[user_id] = client
                me = await client.get_me()
                logger.info(f"✅ Loaded account: {me.first_name} (@{me.username}) ID={user_id}")
            except Exception as e:
                logger.warning(f"Failed to load account {user_id_str}: {e}")

        logger.info(f"📊 Total accounts loaded: {len(self.clients)}/{len(accounts)}")

    def get_all_clients(self) -> list:
        """Get all active clients."""
        return list(self.clients.values())

    def get_client(self, user_id: int) -> TelegramClient:
        return self.clients.get(user_id)

    async def add_client(self, session_name: str, user_id: int):
        """Add a new client to the pool."""
        try:
            client = TelegramClient(session_name, API_ID, API_HASH)
            await client.start()
            self.clients[user_id] = client
            logger.info(f"Added client: {user_id}")
            return client
        except Exception as e:
            logger.error(f"Failed to add client {user_id}: {e}")
            return None

    async def ensure_all_connected(self):
        """Ensure all clients are connected and authorized."""
        for uid, client in list(self.clients.items()):
            try:
                if not client.is_connected():
                    await client.connect()
                if not await client.is_user_authorized():
                    logger.warning(f"Client {uid} not authorized, removing...")
                    del self.clients[uid]
            except Exception as e:
                logger.warning(f"Client {uid} connection error: {e}")


pool = ClientPool()


# ==================== CHANNEL JOIN FLOW ====================
async def check_channel_joined(identifier: str) -> dict:
    """
    Check if channel is already joined.
    Returns: {joined: bool, entity: ..., title: ..., channel_id: ...}
    """
    await pool.ensure_all_connected()

    # Try to resolve the identifier
    try:
        entity = await pool.main_client.get_entity(identifier)
        return {
            "joined": True,
            "entity": entity,
            "title": getattr(entity, 'title', str(entity.id)),
            "channel_id": str(entity.id),
            "username": getattr(entity, 'username', ''),
            "is_public": bool(getattr(entity, 'username', ''))
        }
    except errors.ChannelPrivateError:
        return {"joined": False, "reason": "private", "message": "Channel is private — need invite link"}
    except ValueError:
        return {"joined": False, "reason": "not_found", "message": "Channel not found"}
    except Exception as e:
        return {"joined": False, "reason": "error", "message": str(e)}


async def join_with_all_accounts(identifier: str, is_invite: bool = False) -> dict:
    """
    Join a channel with ALL connected accounts.
    Returns detailed results per account.
    """
    await pool.ensure_all_connected()

    results = {}
    success_count = 0
    fail_count = 0

    for user_id, client in pool.clients.items():
        try:
            if is_invite:
                # Invite link join
                if identifier.startswith('https://t.me/+'):
                    hash_part = identifier.split('/')[-1].replace('+', '')
                    try:
                        update = await client(ImportChatInviteRequest(hash=hash_part))
                        entity = update.chats[0] if update.chats else None
                    except errors.InviteHashExpiredError:
                        results[str(user_id)] = {"success": False, "error": "Invite expired"}
                        fail_count += 1
                        continue
                    except errors.InviteHashInvalidError:
                        results[str(user_id)] = {"success": False, "error": "Invalid invite"}
                        fail_count += 1
                        continue
                elif identifier.startswith('https://t.me/'):
                    username = identifier.replace('https://t.me/', '').split('?')[0]
                    entity = await client.get_entity(username)
                else:
                    entity = await client.get_entity(identifier)
            else:
                # Public channel/username
                entity = await client.get_entity(identifier)

            try:
                await client(JoinChannelRequest(entity))
                results[str(user_id)] = {"success": True, "message": "Joined"}
                success_count += 1
            except errors.FloodWaitError as e:
                results[str(user_id)] = {"success": False, "error": f"Flood wait {e.seconds}s"}
                fail_count += 1
            except Exception as e:
                if "already" in str(e).lower() or "participant" in str(e).lower():
                    results[str(user_id)] = {"success": True, "message": "Already joined"}
                    success_count += 1
                else:
                    results[str(user_id)] = {"success": False, "error": str(e)}
                    fail_count += 1

        except errors.FloodWaitError as e:
            results[str(user_id)] = {"success": False, "error": f"Flood wait {e.seconds}s"}
            fail_count += 1
        except Exception as e:
            results[str(user_id)] = {"success": False, "error": str(e)}
            fail_count += 1

        # Small delay between joins to avoid flood
        await asyncio.sleep(1.5)

    channel_entity = None
    try:
        channel_entity = await pool.main_client.get_entity(identifier)
    except:
        pass

    return {
        "success": success_count > 0,
        "total_accounts": len(pool.clients),
        "joined": success_count,
        "failed": fail_count,
        "per_account": results,
        "channel_id": str(getattr(channel_entity, 'id', '')),
        "title": getattr(channel_entity, 'title', identifier),
        "username": getattr(channel_entity, 'username', '')
    }


async def save_channel(channel_id: str, title: str, username: str = '', identifier: str = '') -> dict:
    """Save channel to database for monitoring."""
    channel_info = {
        "id": channel_id,
        "title": title,
        "username": username,
        "identifier": identifier,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "is_active": True,
        "total_dm_sent": 0,
        "session_dm_sent": 0,
        "is_currently_live": False,
        "current_viewers": 0,
        "last_live_at": None,
        "total_accounts_at_join": len(pool.clients)
    }

    fb_update(f'channels/{channel_id}', channel_info)
    logger.info(f"Channel saved: {title} ({channel_id})")
    return channel_info


# ==================== LIVE DETECTION ====================
async def is_channel_live(client: TelegramClient, channel_id: str) -> tuple:
    """
    Detect if channel is live using multiple methods:
    1. Check for active voice/video chat
    2. Recent message timestamps + views
    3. Message action types (pinned messages, service actions)
    4. Message content keywords
    Returns: (is_live: bool, viewers: list)
    """
    try:
        # Handle both numeric channel IDs and usernames
        cid = channel_id.strip()
        if cid.lstrip('-').isdigit():
            from telethon.tl.types import PeerChannel
            entity = await client.get_entity(PeerChannel(int(cid)))
        else:
            entity = await client.get_entity(cid)

        # Method 1: Check full channel info for active call/stream
        try:
            full = await client(functions.channels.GetFullChannelRequest(channel=entity))
            if full.full_chat:
                call = getattr(full.full_chat, 'call', None)
                if call and hasattr(call, 'id'):
                    logger.info(f"🔴 Voice/video chat active in {getattr(entity, 'title', channel_id)}")
                    # Also try to get participants as viewers from the call
                    viewers_from_call = []
                    try:
                        participants = await client.get_participants(entity, limit=100)
                        for p in participants:
                            if p.id not in pool.clients:
                                viewers_from_call.append(p.id)
                        logger.info(f"👥 Found {len(viewers_from_call)} potential viewers from participants")
                    except Exception:
                        pass
                    return (True, viewers_from_call)
        except Exception as e:
            err_str = str(e)
            if 'private' in err_str.lower() or 'CHANNEL_PRIVATE' in err_str.upper():
                logger.warning(f"⚠️ Account not member of channel — can't check live call")
            else:
                logger.debug(f"GetFullChannel error: {err_str[:100]}")

        # Method 2: Get recent messages and check timestamps + views
        messages = await client.get_messages(entity, limit=20)
        viewers = set()
        live_found = False
        recent_msg_count = 0

        now = datetime.now(timezone.utc)

        for msg in messages:
            if msg is None:
                continue

            # Check if message is recent (within last 3 minutes)
            if msg.date:
                msg_time = msg.date.replace(tzinfo=timezone.utc) if msg.date.tzinfo is None else msg.date
                age_seconds = (now - msg_time).total_seconds()
            else:
                age_seconds = 999

            text = (msg.message or '').lower()

            # Method 3: High views on very recent messages = likely live
            if msg.views and msg.views > 20 and age_seconds < 180:
                live_found = True
                recent_msg_count += 1

            # Method 4: Check message actions (live stream started, pinned, etc.)
            if msg.action:
                action_str = str(msg.action)
                if any(kw in action_str for kw in [
                    'LiveStream', 'live_stream', 'started_live',
                    'broadcast', 'video_chat', 'voice_chat',
                    'group_call', 'inviteToGroupCall'
                ]):
                    live_found = True
                    logger.info(f"🔴 Live action detected: {action_str[:80]}")

            # Method 5: Keyword-based detection (fallback)
            live_keywords = [
                '🔴 live', '🔴live', 'stream started', 'is live', 'live now',
                'broadcasting', '#live', 'live stream', 'went live',
                'starting live', 'live on', 'streaming now', 'we are live',
                'live broadcast', 'live show', 'chaliye live', 'live aa gaye'
            ]
            if any(kw in text for kw in live_keywords):
                live_found = True

            # Method 6: Check for forwarded message from "live" channel
            if msg.fwd_from:
                fwd_str = str(msg.fwd_from)
                if 'live' in fwd_str.lower():
                    live_found = True

            # Collect viewer IDs from recent messages
            if msg.from_id and age_seconds < 600:  # last 10 minutes
                try:
                    if hasattr(msg.from_id, 'user_id'):
                        uid = msg.from_id.user_id
                        if uid not in pool.clients:
                            viewers.add(uid)
                except:
                    pass

            # Collect viewers from reactions/forwards
            if msg.forwards and msg.forwards > 5 and age_seconds < 300:
                live_found = True

        # Method 7: If 5+ very recent messages, assume live
        if recent_msg_count >= 5:
            live_found = True

        # Also get recent reactions to expand viewer list
        if live_found:
            try:
                recent = await client.get_messages(entity, limit=50)
                for msg in recent:
                    if msg and msg.from_id:
                        try:
                            if hasattr(msg.from_id, 'user_id'):
                                uid = msg.from_id.user_id
                                if uid not in pool.clients:
                                    viewers.add(uid)
                        except:
                            pass
            except:
                pass

        if live_found:
            logger.info(f"🔴 LIVE: {getattr(entity, 'title', channel_id)} - {len(viewers)} viewers, {recent_msg_count} recent msgs")

        return (live_found, list(viewers))

    except Exception as e:
        logger.error(f"Live check error for {channel_id}: {e}")
        return (False, [])


# ==================== DM SENDING FROM ALL ACCOUNTS ====================
def get_dm_config() -> dict:
    """Get DM message configuration."""
    return fb_get('dm_config', {}) or {}


def has_config_message() -> bool:
    """Check if DM message is configured."""
    config = get_dm_config()
    return bool(config.get('message', '').strip())


def has_user_received_dm(channel_id: str, user_id: int) -> bool:
    """Check if user already received DM from this channel."""
    sent = fb_get(f'dm_sent/{channel_id}', {}) or {}
    return str(user_id) in sent


def mark_user_dmed(channel_id: str, user_id: int, account_id: int):
    """Mark that a user received DM from a specific account."""
    fb_set(f'dm_sent/{channel_id}/{user_id}', {
        "dmed_at": datetime.now(timezone.utc).isoformat(),
        "from_account": account_id
    })


async def send_dm_from_client(client: TelegramClient, user_id: int, message: str,
                               media_url: str = None) -> bool:
    """Send DM to a user from a specific client."""
    try:
        entity = await client.get_input_entity(user_id)

        if media_url and media_url.startswith('http'):
            # Send with image URL
            try:
                await client.send_message(entity, message, file=media_url)
            except:
                await client.send_message(entity, message)
        else:
            await client.send_message(entity, message)

        return True
    except errors.PeerFloodError:
        return False
    except errors.UserPrivacyRestrictedError:
        return False
    except errors.UserBlockedError:
        return False
    except errors.InputUserDeactivatedError:
        return False
    except Exception as e:
        logger.debug(f"DM failed to {user_id}: {e}")
        return False


async def send_dm_from_all_accounts(channel_id: str, user_id: int, channel_info: dict) -> dict:
    """
    Send DM to a viewer from ALL connected accounts.
    Each account sends the message once per user.
    """
    if has_user_received_dm(channel_id, user_id):
        return {"sent": False, "reason": "already_dmed"}

    config = get_dm_config()
    message = config.get('message', '')
    media = config.get('media', {})

    if not message.strip():
        return {"sent": False, "reason": "no_message_configured"}

    media_url = media.get('url', '') if media else ''

    await pool.ensure_all_connected()

    sent_count = 0
    fail_count = 0
    details = {}

    for account_id, client in pool.clients.items():
        # Small random delay between sends
        await asyncio.sleep(random.uniform(1.0, 3.0))

        success = await send_dm_from_client(client, user_id, message, media_url)
        details[str(account_id)] = "sent" if success else "failed"
        if success:
            sent_count += 1
        else:
            fail_count += 1

    # Mark as DMed across all accounts
    mark_user_dmed(channel_id, user_id, list(pool.clients.keys())[0] if pool.clients else 0)

    # Update channel stats
    total = fb_get(f'channels/{channel_id}/total_dm_sent', 0) or 0
    fb_set(f'channels/{channel_id}/total_dm_sent', total + sent_count)
    sess = fb_get(f'channels/{channel_id}/session_dm_sent', 0) or 0
    fb_set(f'channels/{channel_id}/session_dm_sent', sess + sent_count)

    return {
        "sent": sent_count > 0,
        "accounts_used": sent_count,
        "accounts_failed": fail_count,
        "total_accounts": len(pool.clients),
        "details": details
    }


async def send_dms_to_all_viewers(channel_id: str, viewers: list, channel_info: dict) -> dict:
    """Send DMs to all viewers from all accounts."""
    total_sent = 0
    total_failed = 0
    users_dmed = 0
    users_skipped = 0

    for user_id in viewers:
        # Skip our own accounts
        if user_id in pool.clients:
            continue

        result = await send_dm_from_all_accounts(channel_id, user_id, channel_info)
        if result.get("sent"):
            users_dmed += 1
            total_sent += result.get("accounts_used", 0)
        elif result.get("reason") == "already_dmed":
            users_skipped += 1
        else:
            total_failed += result.get("accounts_failed", 0)

    return {
        "users_dmed": users_dmed,
        "users_skipped": users_skipped,
        "total_dms_sent": total_sent,
        "total_failed": total_failed,
        "total_viewers": len(viewers)
    }


# ==================== MONITORING LOOP ====================
monitoring_tasks: dict = {}  # channel_id -> asyncio.Task
active_lives: dict = {}  # channel_id -> session info
MONITOR_DELAY = 5  # seconds between checks (fast detection)


async def monitor_channel(channel_id: str):
    """Continuous monitoring loop for one channel."""
    global active_lives

    channel_info = fb_get(f'channels/{channel_id}', {})
    title = channel_info.get('title', channel_id)
    logger.info(f"🔍 Monitoring started: {title} (checking every {MONITOR_DELAY}s)")

    consecutive_errors = 0

    while True:
        try:
            await pool.ensure_all_connected()

            if not pool.clients:
                logger.warning(f"⚠️ No clients available for {title}, sleeping...")
                await asyncio.sleep(MONITOR_DELAY)
                continue

            # Try ALL clients for checking
            is_live = False
            viewers = []
            last_error = None

            for uid, check_client in pool.clients.items():
                try:
                    is_live, viewers = await is_channel_live(check_client, channel_id)
                    last_error = None
                    break  # Found a working client
                except Exception as e:
                    last_error = str(e)
                    continue

            if last_error and not is_live:
                consecutive_errors += 1
                if consecutive_errors == 1 or consecutive_errors % 60 == 0:
                    logger.warning(f"⚠️ Live check error for {title}: {last_error}")
            else:
                consecutive_errors = 0

            if is_live:
                if channel_id not in active_lives:
                    # Live just started!
                    active_lives[channel_id] = {
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "viewer_count": len(viewers),
                        "dm_sent_this_session": 0,
                        "total_viewers_processed": 0
                    }
                    fb_update(f'channels/{channel_id}', {
                        "is_currently_live": True,
                        "last_live_at": datetime.now(timezone.utc).isoformat(),
                        "current_viewers": len(viewers)
                    })
                    logger.info(f"🔴 LIVE DETECTED: {title} — {len(viewers)} potential viewers")

                # Check if message is configured
                if not has_config_message():
                    if channel_id not in active_lives or active_lives[channel_id].get('warned_no_msg') != True:
                        logger.warning(f"⚠️ DM not configured for {title} — skipping DMs")
                        if channel_id in active_lives:
                            active_lives[channel_id]['warned_no_msg'] = True
                elif viewers:
                    # Send DMs
                    logger.info(f"📨 Sending DMs to {len(viewers)} users in {title} from {len(pool.clients)} accounts...")
                    result = await send_dms_to_all_viewers(channel_id, viewers, channel_info)
                    active_lives[channel_id]["dm_sent_this_session"] += result["total_dms_sent"]
                    active_lives[channel_id]["total_viewers_processed"] += result["users_dmed"]

                    fb_update(f'channels/{channel_id}', {
                        "current_viewers": len(viewers),
                        "session_dm_sent": active_lives[channel_id]["dm_sent_this_session"]
                    })

                    logger.info(f"✅ DONE {title}: {result['users_dmed']} users DMed ({result['total_dms_sent']} msgs), "
                               f"{result['users_skipped']} skipped")
                else:
                    logger.debug(f"🔴 LIVE but no viewers yet: {title}")
            else:
                # Channel went offline
                if channel_id in active_lives:
                    session_info = active_lives.pop(channel_id)
                    fb_update(f'channels/{channel_id}', {
                        "is_currently_live": False,
                        "current_viewers": 0,
                        "session_dm_sent": 0
                    })
                    logger.info(f"⚫ Live ended: {title} — {session_info['dm_sent_this_session']} DMs sent this session")

            await asyncio.sleep(MONITOR_DELAY)

        except asyncio.CancelledError:
            logger.info(f"Monitoring cancelled: {title}")
            break
        except Exception as e:
            logger.error(f"Monitor loop error for {title}: {e}")
            await asyncio.sleep(MONITOR_DELAY * 2)


async def start_monitoring(channel_id: str):
    """Start monitoring a channel."""
    if channel_id in monitoring_tasks:
        monitoring_tasks[channel_id].cancel()
    task = asyncio.create_task(monitor_channel(channel_id))
    monitoring_tasks[channel_id] = task
    logger.info(f"Monitoring task created for channel {channel_id}")


async def start_all_monitoring():
    """Start monitoring all saved channels."""
    channels = fb_get('channels', {}) or {}
    for ch_id in channels:
        await start_monitoring(ch_id)
    logger.info(f"All monitoring started: {len(monitoring_tasks)} channels")


async def stop_monitoring(channel_id: str):
    """Stop monitoring a channel."""
    if channel_id in monitoring_tasks:
        monitoring_tasks[channel_id].cancel()
        del monitoring_tasks[channel_id]
    if channel_id in active_lives:
        del active_lives[channel_id]


# ==================== STATS ====================
def get_full_stats() -> dict:
    """Get complete bot statistics."""
    channels = fb_get('channels', {}) or {}
    accounts = fb_get('accounts', {}) or {}
    dm_config = get_dm_config()

    total_dm_sent = sum(ch.get('total_dm_sent', 0) for ch in channels.values())
    currently_live = sum(1 for ch in channels.values() if ch.get('is_currently_live'))

    channel_list = []
    for ch_id, ch in channels.items():
        dm_records = fb_get(f'dm_sent/{ch_id}', {}) or {}
        channel_list.append({
            "id": ch_id,
            "title": ch.get('title', 'Unknown'),
            "username": ch.get('username', ''),
            "is_live": ch.get('is_currently_live', False),
            "total_dm_sent": ch.get('total_dm_sent', 0),
            "session_dm_sent": ch.get('session_dm_sent', 0),
            "unique_dmed": len(dm_records),
            "current_viewers": ch.get('current_viewers', 0),
            "joined_at": ch.get('added_at', ''),
            "total_accounts": ch.get('total_accounts_at_join', 0)
        })

    return {
        "total_channels": len(channels),
        "total_accounts": len(accounts),
        "total_loaded_clients": len(pool.clients),
        "active_lives": currently_live,
        "total_dm_sent": total_dm_sent,
        "dm_configured": bool(dm_config.get('message', '').strip()),
        "dm_message": dm_config.get('message', ''),
        "has_media": bool(dm_config.get('media')),
        "channels": channel_list,
        "accounts": list(accounts.values()),
        "admins": fb_get('admins', [])
    }


# ==================== BOT COMMANDS ====================
async def setup_bot_commands():
    """Register bot command handlers."""

    @pool.bot_client.on(events.NewMessage(pattern='/start'))
    async def cmd_start(event):
        await event.respond(
            f"🤖 **Live Stream Monitor Bot v3**\n\n"
            f"Welcome {event.sender.first_name}!\n\n"
            f"📺 /channels — List channels\n"
            f"👤 /accounts — Connected accounts\n"
            f"📊 /stats — Statistics\n"
            f"💬 /setmsg <text> — Set DM message\n"
            f"🔄 /resetdm — Reset DM records\n"
            f"➕ /addchannel <link> — Add channel\n"
            f"ℹ️ /help — Help"
        )

    @pool.bot_client.on(events.NewMessage(pattern='/stats'))
    async def cmd_stats(event):
        stats = get_full_stats()
        msg = (f"📊 **Stats**\n\n"
               f"👤 Accounts: {stats['total_loaded_clients']}\n"
               f"📺 Channels: {stats['total_channels']}\n"
               f"🔴 Live: {stats['active_lives']}\n"
               f"✉️ Total DMs: {stats['total_dm_sent']}\n"
               f"💬 Msg set: {'✅' if stats['dm_configured'] else '❌'}")
        await event.respond(msg)

    @pool.bot_client.on(events.NewMessage(pattern='/channels'))
    async def cmd_channels(event):
        stats = get_full_stats()
        if not stats['channels']:
            await event.respond("No channels added.")
            return
        msg = "📺 **Channels:**\n\n"
        for ch in stats['channels']:
            s = "🔴 LIVE" if ch['is_live'] else "⚫"
            msg += f"• {ch['title']} {s} — {ch['total_dm_sent']} DMs\n"
        await event.respond(msg)

    @pool.bot_client.on(events.NewMessage(pattern=r'/setmsg (.+)'))
    async def cmd_setmsg(event):
        text = event.pattern_match.group(1)
        fb_update('dm_config', {'message': text})
        await event.respond(f"✅ DM message set!")

    @pool.bot_client.on(events.NewMessage(pattern='/resetdm'))
    async def cmd_resetdm(event):
        fb_delete('dm_sent')
        await event.respond("✅ DM records reset.")

    @pool.bot_client.on(events.NewMessage(pattern='/help'))
    async def cmd_help(event):
        await event.respond(
            "📖 **Help**\n\n"
            "1. Add accounts via Web Dashboard\n"
            "2. Add channels — bot joins with ALL accounts\n"
            "3. Set DM message (required!)\n"
            "4. Bot auto-detects live streams & sends DMs\n\n"
            "⚠️ DM message must be set before monitoring!\n"
            "Each viewer gets DM from ALL accounts."
        )

    logger.info("Bot commands registered")


# ==================== MAIN ====================
async def main():
    logger.info("=" * 50)
    logger.info("Telegram Live Stream Monitor Bot v3")
    logger.info("=" * 50)

    load_local_db()
    init_firebase()

    await pool.init_main()
    await pool.load_all_accounts()
    await start_all_monitoring()

    logger.info(f"✅ Bot ready! {len(pool.clients)} accounts, {len(monitoring_tasks)} channels monitored")
    logger.info(f"Admin ID: {ADMIN_IDS}")

    if not has_config_message():
        logger.warning("⚠️ DM message not set! DMs will NOT be sent until configured.")
    if not pool.clients:
        logger.critical("❌ NO ACCOUNTS LOADED! Add accounts via web dashboard first!")

    await pool.main_client.run_until_disconnected()


def run_bot():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")


if __name__ == "__main__":
    run_bot()

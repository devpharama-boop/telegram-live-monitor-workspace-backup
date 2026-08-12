#!/usr/bin/env python3
"""
Web Dashboard for Telegram Live Stream Monitor Bot
Flask-based web UI for managing the bot.
"""

import os
import sys
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
from functools import wraps
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, db

load_dotenv()

# Add parent dir to path for bot imports
sys.path.insert(0, str(Path(__file__).parent))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "telegram-live-monitor-secret-key-2024")

# ==================== PASSWORD PROTECTION ====================
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "tinesh")

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page."""
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == DASHBOARD_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error='❌ Wrong password!')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))

def require_auth(f):
    """Decorator to require authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"success": False, "error": "Authentication required", "auth_required": True}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ==================== CONFIG ====================
ADMIN_IDS = [5844447576]
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "")
FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "firebase-cred.json")

# ==================== FIREBASE ====================
firebase_ref = None

try:
    if FIREBASE_DB_URL and os.path.exists(FIREBASE_CRED_PATH):
        cred = credentials.Certificate(FIREBASE_CRED_PATH)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        firebase_ref = db.reference('/')
        print("Firebase connected for web dashboard")
except Exception as e:
    print(f"Firebase init warning: {e}")

# Local JSON fallback
LOCAL_DB_PATH = Path("local_db.json")


def fb_get(path, default=None):
    if firebase_ref:
        return firebase_ref.child(path).get() or default
    return local_get(path, default)


def fb_set(path, value):
    if firebase_ref:
        firebase_ref.child(path).set(value)
    else:
        local_set(path, value)


def fb_update(path, value):
    if firebase_ref:
        firebase_ref.child(path).update(value)
    else:
        current = local_get(path, {}) or {}
        current.update(value)
        local_set(path, current)


def fb_delete(path):
    if firebase_ref:
        firebase_ref.child(path).delete()
    else:
        local_delete(path)


# Local JSON helpers
def _load_local():
    if LOCAL_DB_PATH.exists():
        with open(LOCAL_DB_PATH, 'r') as f:
            return json.load(f)
    return {}


def _save_local(data):
    with open(LOCAL_DB_PATH, 'w') as f:
        json.dump(data, f, indent=2, default=str)


def local_get(path, default=None):
    d = _load_local()
    parts = path.strip('/').split('/')
    for part in parts:
        if isinstance(d, dict) and part in d:
            d = d[part]
        else:
            return default
    return d


def local_set(path, value):
    d = _load_local()
    parts = path.strip('/').split('/')
    current = d
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value
    _save_local(d)


def local_delete(path):
    d = _load_local()
    parts = path.strip('/').split('/')
    current = d
    for part in parts[:-1]:
        if part not in current:
            return
        current = current[part]
    if parts[-1] in current:
        del current[parts[-1]]
    _save_local(d)


# ==================== AUTH ====================
def is_admin(user_id):
    admins = fb_get('admins', []) or []
    return int(user_id) in admins or int(user_id) in ADMIN_IDS


# ==================== MAIN DASHBOARD ====================
@app.route('/')
@require_auth
def index():
    """Main dashboard page."""
    return render_template('dashboard.html')


# ==================== API ROUTES ====================
@app.route('/api/stats')
@require_auth
def api_stats():
    """Get bot statistics."""
    channels = fb_get('channels', {}) or {}
    accounts = fb_get('accounts', {}) or {}
    dm_config = fb_get('dm_config', {})
    active_lives = fb_get('active_lives', {})

    # Count DMs sent
    total_dm_sent = sum(
        ch.get('total_dm_sent', 0) for ch in channels.values()
    )
    currently_live = sum(
        1 for ch in channels.values() if ch.get('is_currently_live')
    )

    channel_list = []
    for ch_id, ch in channels.items():
        dm_sent_for_ch = fb_get(f'dm_sent/{ch_id}', {}) or {}
        channel_list.append({
            "id": ch_id,
            "title": ch.get('title', 'Unknown'),
            "username": ch.get('username', ''),
            "is_live": ch.get('is_currently_live', False),
            "total_dm_sent": ch.get('total_dm_sent', 0),
            "session_dm_sent": ch.get('session_dm_sent', 0),
            "unique_dmed": len(dm_sent_for_ch),
            "joined_at": ch.get('added_at', ''),
            "current_viewers": ch.get('current_viewers', 0),
            "total_accounts": ch.get('total_accounts_at_join', 0)
        })

    return jsonify({
        "success": True,
        "data": {
            "total_channels": len(channels),
            "total_accounts": len(accounts),
            "active_lives": currently_live,
            "total_dm_sent": total_dm_sent,
            "dm_configured": bool(dm_config.get('message', '').strip()),
            "dm_message": dm_config.get('message', ''),
            "has_media": bool(dm_config.get('media')),
            "channels": channel_list,
            "accounts": list(accounts.values()),
            "admins": fb_get('admins', [])
        }
    })


@app.route('/api/channels', methods=['GET', 'POST'])
@require_auth
def api_channels():
    """List channels or add a new one."""
    if request.method == 'GET':
        channels = fb_get('channels', {}) or {}
        return jsonify({"success": True, "channels": list(channels.values())})

    # POST - Add channel (full flow: check + join all accounts + save)
    data = request.get_json()
    channel_input = data.get('channel_input', '').strip()
    invite_link = data.get('invite_link', '').strip()
    is_invite = data.get('is_invite', False)

    identifier = invite_link if is_invite and invite_link else channel_input
    if not identifier:
        return jsonify({"success": False, "error": "Channel username, ID, or invite link required"})

    # Detect if it's a numeric channel ID (e.g., -1004368116984)
    is_channel_id = identifier.lstrip('-').isdigit()

    API_ID = int(os.getenv("TELEGRAM_API_ID", "35812449"))
    API_HASH = os.getenv("TELEGRAM_API_HASH", "099cfed535a5b2dcd8e43f157d30e3ce")

    async def _add_channel():
        # Load all accounts
        accounts = fb_get('accounts', {}) or {}
        if not accounts:
            return {"success": False, "error": "No accounts connected. Add an account first."}

        client_pool = {}
        for uid_str, acc in accounts.items():
            try:
                session_name = acc.get('session_name', f'account_{uid_str}')
                client = TelegramClient(session_name, API_ID, API_HASH)
                await client.start()
                client_pool[int(uid_str)] = client
            except Exception as e:
                pass

        if not client_pool:
            return {"success": False, "error": "Could not load any accounts"}

        # Step 1: Check if already joined
        entity = None
        joined_already = False
        try:
            if is_channel_id:
                from telethon.tl.types import PeerChannel
                entity = await list(client_pool.values())[0].get_entity(PeerChannel(int(identifier)))
                joined_already = True
            else:
                entity = await list(client_pool.values())[0].get_entity(identifier)
                joined_already = True
        except Exception:
            joined_already = False

        # Step 2: Join with ALL accounts
        join_results = {}
        success_count = 0
        for uid, client in client_pool.items():
            try:
                if is_invite:
                    hash_part = identifier.split('/')[-1].replace('+', '')
                    try:
                        update = await client(ImportChatInviteRequest(hash=hash_part))
                        entity = update.chats[0] if update.chats else entity
                    except errors.InviteHashExpiredError:
                        join_results[str(uid)] = {"success": False, "error": "Invite expired"}
                        continue
                    except errors.InviteHashInvalidError:
                        join_results[str(uid)] = {"success": False, "error": "Invalid invite"}
                        continue
                elif is_channel_id:
                    from telethon.tl.types import PeerChannel
                    entity = await client.get_entity(PeerChannel(int(identifier)))
                else:
                    entity = await client.get_entity(identifier)

                try:
                    await client(JoinChannelRequest(entity))
                    join_results[str(uid)] = {"success": True, "message": "Joined"}
                    success_count += 1
                except Exception as e:
                    if "already" in str(e).lower():
                        join_results[str(uid)] = {"success": True, "message": "Already joined"}
                        success_count += 1
                    else:
                        join_results[str(uid)] = {"success": False, "error": str(e)}

            except Exception as e:
                join_results[str(uid)] = {"success": False, "error": str(e)}

            await asyncio.sleep(1.5)

        # Disconnect all
        for client in client_pool.values():
            await client.disconnect()

        # Step 3: Save channel info
        ch_id = str(getattr(entity, 'id', '')) if entity else identifier
        title = getattr(entity, 'title', identifier) if entity else identifier
        username = getattr(entity, 'username', '') if entity else ''

        channel_info = {
            "id": ch_id,
            "title": title,
            "username": username,
            "identifier": identifier,
            "is_invite": is_invite,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "is_active": True,
            "total_dm_sent": 0,
            "session_dm_sent": 0,
            "is_currently_live": False,
            "current_viewers": 0,
            "total_accounts_at_join": len(accounts),
            "last_live_at": None
        }
        fb_update(f'channels/{ch_id}', channel_info)

        return {
            "success": True,
            "was_already_joined": joined_already,
            "accounts_joined": success_count,
            "total_accounts": len(accounts),
            "channel": channel_info,
            "join_details": join_results
        }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_add_channel())
    loop.close()
    return jsonify(result)


@app.route('/api/channels/<channel_id>', methods=['DELETE'])
@require_auth
def api_delete_channel(channel_id):
    """Remove a channel."""
    fb_delete(f'channels/{channel_id}')
    fb_delete(f'dm_sent/{channel_id}')
    return jsonify({"success": True, "message": "Channel removed"})


@app.route('/api/dm/config', methods=['GET', 'POST'])
@require_auth
def api_dm_config():
    """Get or set DM configuration."""
    if request.method == 'GET':
        config = fb_get('dm_config', {})
        return jsonify({"success": True, "config": config})

    # POST - Set DM message (full save, not merge)
    data = request.get_json()
    message = data.get('message', '')
    image_url = data.get('image_url', '')
    media_file = data.get('media_file', '')

    # Get existing config to preserve other fields
    existing = fb_get('dm_config', {}) or {}

    # Build new config
    new_config = dict(existing)  # preserve existing fields
    if message:
        new_config['message'] = message
    if image_url or media_file:
        new_config['media'] = {
            'type': 'image',
            'url': image_url or media_file,
            'file_path': media_file
        }

    # Force full save (not merge) to ensure all fields persist
    fb_set('dm_config', new_config)
    return jsonify({"success": True, "message": "DM config updated", "config": new_config})


@app.route('/api/dm/reset', methods=['POST'])
@require_auth
def api_reset_dm():
    """Reset DM records."""
    data = request.get_json() or {}
    channel_id = data.get('channel_id')

    if channel_id:
        fb_delete(f'dm_sent/{channel_id}')
        fb_set(f'channels/{channel_id}/total_dm_sent', 0)
    else:
        fb_delete('dm_sent')

    return jsonify({"success": True, "message": "DM records reset"})


@app.route('/api/dm/reset-config', methods=['POST'])
@require_auth
def api_reset_dm_config():
    """Reset DM message config to default."""
    fb_set('dm_config', {
        'message': "👋 Hi! I noticed you're watching this live stream. Check out our community!"
    })
    return jsonify({"success": True, "message": "DM config reset to default"})


@app.route('/api/accounts', methods=['GET'])
@require_auth
def api_accounts():
    """Get connected accounts."""
    accounts = fb_get('accounts', {}) or {}
    return jsonify({"success": True, "accounts": list(accounts.values())})


# ==================== ACCOUNT LOGIN (Built-in, no bot.py import) ====================
# We handle login directly here to avoid cross-module database conflicts
import asyncio
from telethon import TelegramClient, errors as telethon_errors

# In-memory pending logins (avoids file I/O race condition)
_pending_logins: dict = {}


@app.route('/api/accounts/login', methods=['POST'])
@require_auth
def api_accounts_login():
    """Login a new account with phone number."""
    data = request.get_json()
    phone = data.get('phone', '').strip()

    if not phone:
        return jsonify({"success": False, "error": "Phone number required"})

    API_ID = int(os.getenv("TELEGRAM_API_ID", "35812449"))
    API_HASH = os.getenv("TELEGRAM_API_HASH", "099cfed535a5b2dcd8e43f157d30e3ce")

    async def _login():
        session_name = f"account_{phone.replace('+', '').replace(' ', '').replace('-', '')}"
        client = TelegramClient(session_name, API_ID, API_HASH)
        try:
            await client.connect()
            sent_code = await client.send_code_request(phone)
            _pending_logins[phone] = {
                "phone_code_hash": sent_code.phone_code_hash,
                "session_name": session_name,
                "attempted_at": datetime.now(timezone.utc).isoformat()
            }
            # Also save to DB for persistence across restarts
            fb_set(f'pending_accounts/{phone}', _pending_logins[phone])
            await client.disconnect()
            return {"success": True, "message": "OTP sent successfully"}
        except Exception as e:
            await client.disconnect()
            return {"success": False, "error": str(e)}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_login())
    loop.close()
    return jsonify(result)


@app.route('/api/accounts/verify', methods=['POST'])
@require_auth
def api_accounts_verify():
    """Verify OTP for account login."""
    data = request.get_json()
    phone = data.get('phone', '').strip()
    otp = data.get('otp', '').strip()
    password = data.get('password', '').strip()

    if not phone or not otp:
        return jsonify({"success": False, "error": "Phone and OTP required"})

    # Get pending login data — check in-memory first, then DB
    pending = _pending_logins.get(phone)
    if not pending:
        pending = fb_get(f'pending_accounts/{phone}')
    if not pending:
        return jsonify({
            "success": False,
            "error": "No pending login for this number. Please send OTP again first."
        })

    API_ID = int(os.getenv("TELEGRAM_API_ID", "35812449"))
    API_HASH = os.getenv("TELEGRAM_API_HASH", "099cfed535a5b2dcd8e43f157d30e3ce")

    async def _verify():
        session_name = pending['session_name']
        phone_code_hash = pending['phone_code_hash']
        client = TelegramClient(session_name, API_ID, API_HASH)
        try:
            await client.connect()
            authed = False
            try:
                await client.sign_in(
                    phone=phone,
                    code=otp,
                    phone_code_hash=phone_code_hash
                )
                authed = True
            except telethon_errors.SessionPasswordNeededError:
                if not password:
                    await client.disconnect()
                    return {"success": False, "error": "2FA password required", "need_password": True}
                await client.sign_in(password=password)
                authed = True

            if authed:
                me = await client.get_me()
                # SAVE SESSION STRING for Railway persistence
                session_str = client.session.save()
                print(f"🔑 Session captured for {me.first_name} (ID={me.id})")
                
                account_info = {
                    "phone": phone,
                    "user_id": me.id,
                    "first_name": me.first_name or "Unknown",
                    "username": me.username or "",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                    "session_name": session_name,
                    "session_string": session_str,
                    "is_active": True
                }
                # Save account
                fb_update(f'accounts/{me.id}', account_info)
                # Also save session separately for bot.py
                fb_set(f'sessions/{me.id}', session_str)
                # Clear pending
                _pending_logins.pop(phone, None)
                fb_delete(f'pending_accounts/{phone}')
                await client.disconnect()
                return {"success": True, "account": account_info}
            else:
                await client.disconnect()
                return {"success": False, "error": "Login failed"}

        except telethon_errors.PhoneCodeInvalidError:
            await client.disconnect()
            return {"success": False, "error": "Invalid OTP code. Please try again."}
        except telethon_errors.PhoneCodeExpiredError:
            await client.disconnect()
            _pending_logins.pop(phone, None)
            fb_delete(f'pending_accounts/{phone}')
            return {"success": False, "error": "OTP expired. Please send a new OTP."}
        except telethon_errors.PasswordHashInvalidError:
            await client.disconnect()
            return {"success": False, "error": "Incorrect 2FA password."}
        except telethon_errors.FloodWaitError as e:
            await client.disconnect()
            return {"success": False, "error": f"Too many attempts. Wait {e.seconds} seconds."}
        except Exception as e:
            await client.disconnect()
            return {"success": False, "error": f"Verification failed: {str(e)}"}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_verify())
    loop.close()
    return jsonify(result)


@app.route('/api/admins', methods=['GET', 'POST'])
@require_auth
def api_admins():
    """Get admins or add a new admin."""
    if request.method == 'GET':
        admins = fb_get('admins', []) or []
        return jsonify({"success": True, "admins": admins + ADMIN_IDS})

    # POST - Add admin
    data = request.get_json()
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({"success": False, "error": "User ID required"})

    admins = fb_get('admins', []) or []
    if int(user_id) not in admins and int(user_id) not in ADMIN_IDS:
        admins.append(int(user_id))
        fb_set('admins', admins)
        return jsonify({"success": True, "message": f"Admin {user_id} added"})

    return jsonify({"success": False, "error": "Already an admin"})


@app.route('/api/admins/<int:user_id>', methods=['DELETE'])
@require_auth
def api_remove_admin(user_id):
    """Remove an admin."""
    if user_id in ADMIN_IDS:
        return jsonify({"success": False, "error": "Cannot remove primary admin"})

    admins = fb_get('admins', []) or []
    if user_id in admins:
        admins.remove(user_id)
        fb_set('admins', admins)
        return jsonify({"success": True, "message": f"Admin {user_id} removed"})

    return jsonify({"success": False, "error": "Not an admin"})


@app.route('/api/user/check/<int:user_id>')
@require_auth
def api_check_user(user_id):
    """Check if a user is admin."""
    return jsonify({
        "success": True,
        "is_admin": is_admin(user_id),
        "user_id": user_id
    })


# ==================== RUN ====================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

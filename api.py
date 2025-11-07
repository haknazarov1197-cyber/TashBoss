import os
import json
import logging
from typing import Optional, Any, Dict

from fastapi import FastAPI, Request, status, HTTPException
from fastapi.responses import JSONResponse
import requests
import firebase_admin
from firebase_admin import credentials, firestore

# --------------------------
# 1. SETUP FIREBASE & LOGGER
# --------------------------

# Environment Variables
# The __firebase_config is injected by the environment; we use os.environ['FIREBASE_CONFIG'] as a fallback.
FIREBASE_CONFIG_JSON = os.environ.get('FIREBASE_CONFIG')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Firebase (this logic is typically handled by the runtime environment)
try:
    if FIREBASE_CONFIG_JSON:
        firebase_config = json.loads(FIREBASE_CONFIG_JSON)
        # Check if Firebase is already initialized
        if not firebase_admin._apps:
            cred = credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred)
            logger.info("--- Firebase initialized successfully. ---")
            db = firestore.client()
        else:
            db = firestore.client()
            logger.info("--- Firebase was already initialized. ---")
    else:
        logger.error("--- FIREBASE_CONFIG is missing. Firestore will not be available. ---")
except Exception as e:
    logger.error(f"--- ERROR initializing Firebase: {e} ---")

# --------------------------
# 2. SETUP FASTAPI
# --------------------------
app = FastAPI(title="Crypto Clicker Bot API")

# --------------------------
# 3. HELPER FUNCTIONS
# --------------------------

def send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    """
    Sends a message back to the Telegram user using the Bot API.
    """
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is not set. Cannot send message.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'Markdown'
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status() # Raise exception for bad status codes
        logger.info(f"Message sent to chat {chat_id}. Status: {response.status_code}")
        return True
    except requests.exceptions.HTTPError as e:
        logger.error(f"Telegram API HTTP error: {e}. Response: {response.text}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending message to Telegram: {e}")
        return False

# --------------------------
# 4. BOT WEBHOOK ENDPOINT (UPDATED)
# --------------------------

@app.post("/webhook", status_code=status.HTTP_200_OK)
async def telegram_webhook(request: Request):
    """
    Handles incoming updates from Telegram and processes commands.
    """
    try:
        update = await request.json()
        
        # We only care about messages for now
        if 'message' not in update:
            return JSONResponse({"status": "ok", "message": "No message in update"}, status_code=200)

        message = update['message']
        chat_id = message['chat']['id']
        text = message.get('text', '')
        
        logger.info(f"Received message from chat {chat_id}: {text}")

        # Check for the /start command
        if text.startswith('/start'):
            welcome_text = (
                "Привет! 👋\n\n"
                "Добро пожаловать в нашу игру-кликер!\n"
                "Нажмите на кнопку ниже, чтобы начать играть в браузере (в Telegram Mini App)."
            )
            
            # --- Inline Keyboard to open Mini App (The button that opens your game) ---
            # NOTE: REPLACE YOUR_MINI_APP_URL with the actual URL of your Mini App/Frontend
            # The URL will typically be the same as your primary Render URL (https://tashboss.onrender.com)
            mini_app_url = os.environ.get('MINI_APP_URL', 'https://tashboss.onrender.com') 
            
            reply_markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": "🚀 Начать игру",
                            "web_app": {"url": mini_app_url}
                        }
                    ]
                ]
            }

            send_message(chat_id, welcome_text, reply_markup=reply_markup)
            
        # Add more command handling here (e.g., /balance, /help)
        elif text.startswith('/'):
             send_message(chat_id, "Неизвестная команда. Введите /start для начала игры.")

        # Ignore other text messages for now
        
        return JSONResponse({"status": "ok"}, status_code=200)

    except json.JSONDecodeError:
        logger.error("Error decoding JSON from Telegram webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    except Exception as e:
        logger.error(f"Error processing Telegram webhook: {e}")
        # Telegram expects a 200 response even on internal errors to prevent retries
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=200)

# --------------------------
# 5. API ENDPOINTS (Keep your game logic endpoints)
# --------------------------

@app.get("/")
def read_root():
    return {"message": "Crypto Clicker Bot API is running."}

@app.get("/state")
def get_game_state(user_id: str):
    # Placeholder for reading user state from Firestore
    return {"user_id": user_id, "taps": 0, "level": 1, "balance": 1000}

@app.post("/tap")
def record_tap(user_id: str):
    # Placeholder for updating tap count in Firestore
    return {"user_id": user_id, "taps_added": 1}

@app.post("/upgrade")
def apply_upgrade(user_id: str, upgrade_type: str):
    # Placeholder for applying an upgrade
    return {"user_id": user_id, "upgrade_applied": upgrade_type}

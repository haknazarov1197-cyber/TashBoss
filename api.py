# api.py
import os
import sys
import json
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import firebase_admin
from firebase_admin import credentials, auth, firestore

# Для использования @transactional
from google.cloud import firestore as gcfirestore

# ----------------- Конфигурация Firebase -----------------
SERVICE_ACCOUNT_KEY = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
if not SERVICE_ACCOUNT_KEY:
    print("CRITICAL: FIREBASE_SERVICE_ACCOUNT_KEY environment variable not found.", file=sys.stderr)
    sys.exit(1)

try:
    # Попытка считать как JSON строку, иначе как путь к файлу
    try:
        service_account_info = json.loads(SERVICE_ACCOUNT_KEY)
        cred = credentials.Certificate(service_account_info)
    except Exception:
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY)
    firebase_admin.initialize_app(cred)
except Exception as e:
    print("CRITICAL: Failed to initialize Firebase Admin SDK:", e, file=sys.stderr)
    sys.exit(1)

db = firestore.client()

# ----------------- Настройки игры -----------------
INITIAL_STATE: Dict[str, Any] = {
    "balance": 100.0,
    "sectors": {"sector1": 0, "sector2": 0, "sector3": 0},
    "last_collection_time": datetime.now(timezone.utc),
}

INCOME_RATES = {"sector1": 0.5, "sector2": 2.0, "sector3": 10.0}  # per second per sector
SECTOR_COSTS = {"sector1": 100.0, "sector2": 500.0, "sector3": 2500.0}

# ----------------- FastAPI app -----------------
app = FastAPI()

# CORS: разрешаем всё (критически важно для работы в WebApp)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Сервис статических файлов — корневая директория
app.mount("/static", StaticFiles(directory=".", html=True), name="static")


# Возврат index.html для / и /webapp
@app.get("/", response_class=FileResponse)
@app.get("/webapp", response_class=FileResponse)
async def serve_index():
    return FileResponse("index.html")


# ----------------- Аутентификация -----------------
async def get_auth_data(request: Request) -> Dict[str, Any]:
    """
    Извлекает токен из Authorization: Bearer <token> и верифицирует через Firebase.
    Возвращает decoded token (содержит uid).
    """
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header missing")
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header malformed")
    token = parts[1]
    try:
        decoded = auth.verify_id_token(token)
        return decoded
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid auth token: {str(e)}")


def user_doc_ref(app_id: str, user_id: str):
    """
    Путь: /artifacts/{appId}/users/{userId}/tashboss_clicker/{userId}
    """
    return db.document(f"artifacts/{app_id}/users/{user_id}/tashboss_clicker/{user_id}")


# ----------------- Транзакционные операции -----------------
@gcfirestore.transactional
def _load_state_tx(transaction: gcfirestore.Transaction, doc_ref: gcfirestore.DocumentReference):
    snapshot = doc_ref.get(transaction=transaction)
    if snapshot.exists:
        data = snapshot.to_dict()
        return data
    else:
        init = INITIAL_STATE.copy()
        transaction.set(doc_ref, init)
        return init


@gcfirestore.transactional
def _collect_income_tx(transaction: gcfirestore.Transaction, doc_ref: gcfirestore.DocumentReference):
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        state = INITIAL_STATE.copy()
        transaction.set(doc_ref, state)
        snapshot = doc_ref.get(transaction=transaction)
    state = snapshot.to_dict()

    last_time = state.get("last_collection_time")
    if not isinstance(last_time, datetime):
        try:
            last_time = datetime.fromisoformat(last_time)
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
        except Exception:
            last_time = datetime.now(timezone.utc)

    now = datetime.now(timezone.utc)
    delta_seconds = (now - last_time).total_seconds()

    sectors = state.get("sectors", {"sector1": 0, "sector2": 0, "sector3": 0})
    income = 0.0
    for s, cnt in sectors.items():
        rate = INCOME_RATES.get(s, 0.0)
        income += rate * float(cnt) * delta_seconds

    new_balance = float(state.get("balance", 0.0)) + income
    new_state = dict(state)
    new_state["balance"] = new_balance
    new_state["last_collection_time"] = now

    transaction.set(doc_ref, new_state)

    result = dict(new_state)
    result["collected_income"] = income
    # Преобразуем время к ISO для ответа
    result["last_collection_time"] = now.isoformat()
    return result


@gcfirestore.transactional
def _buy_sector_tx(transaction: gcfirestore.Transaction, doc_ref: gcfirestore.DocumentReference, sector: str):
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        state = INITIAL_STATE.copy()
        transaction.set(doc_ref, state)
        snapshot = doc_ref.get(transaction=transaction)
    state = snapshot.to_dict()

    balance = float(state.get("balance", 0.0))
    sectors = state.get("sectors", {"sector1": 0, "sector2": 0, "sector3": 0})

    if sector not in SECTOR_COSTS:
        raise ValueError("Unknown sector")
    cost = float(SECTOR_COSTS[sector])
    if balance < cost:
        raise ValueError("Insufficient balance")
    balance -= cost
    sectors[sector] = int(sectors.get(sector, 0)) + 1

    new_state = dict(state)
    new_state["balance"] = balance
    new_state["sectors"] = sectors

    transaction.set(doc_ref, new_state)

    if isinstance(new_state.get("last_collection_time"), datetime):
        new_state["last_collection_time"] = new_state["last_collection_time"].isoformat()
    return new_state


# ----------------- API endpoints -----------------
@app.post("/api/load_state")
async def load_state(request: Request):
    auth_data = await get_auth_data(request)
    uid = auth_data.get("uid")
    app_id = auth_data.get("aud", "tashboss_webapp")
    doc_ref = user_doc_ref(app_id, uid)
    transaction = db.transaction()
    try:
        state = _load_state_tx(transaction, doc_ref)
        # Преобразование last_collection_time в isoformat если нужно
        if isinstance(state.get("last_collection_time"), datetime):
            state["last_collection_time"] = state["last_collection_time"].isoformat()
        return JSONResponse({"status": "ok", "state": state})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load state: {str(e)}")


@app.post("/api/collect_income")
async def collect_income(request: Request):
    auth_data = await get_auth_data(request)
    uid = auth_data.get("uid")
    app_id = auth_data.get("aud", "tashboss_webapp")
    doc_ref = user_doc_ref(app_id, uid)
    transaction = db.transaction()
    try:
        result = _collect_income_tx(transaction, doc_ref)
        return JSONResponse({"status": "ok", "state": result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to collect income: {str(e)}")


@app.post("/api/buy_sector")
async def buy_sector(request: Request):
    auth_data = await get_auth_data(request)
    uid = auth_data.get("uid")
    app_id = auth_data.get("aud", "tashboss_webapp")
    body = await request.json()
    sector = body.get("sector")
    if not sector:
        raise HTTPException(status_code=400, detail="Missing 'sector' in request body")
    doc_ref = user_doc_ref(app_id, uid)
    transaction = db.transaction()
    try:
        new_state = _buy_sector_tx(transaction, doc_ref, sector)
        return JSONResponse({"status": "ok", "state": new_state})
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to buy sector: {str(e)}")


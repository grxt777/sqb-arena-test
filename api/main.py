"""
ATM Monitor — FastAPI Backend
==============================

REST endpoints:
  GET  /api/atms                    → все ATM + текущий баланс + ML-прогноз
  GET  /api/atms/{atm_id}           → детали одного ATM + история
  GET  /api/atms/{atm_id}/history   → история баланса (last_n шагов)
  GET  /api/predictions             → ML-прогнозы для всех ATM
  GET  /api/alerts                  → ATM в критическом/warning состоянии
  POST /api/route                   → оптимальный маршрут инкассации
  GET  /api/sim/status              → статус симуляции
  POST /api/sim/speed               → изменить скорость симуляции

WebSocket:
  WS   /ws/live                     → live-поток (push каждые SIM_TICK_SECS)

Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config import SIM_TICK_SECS, BASE_DIR
from core.simulator import simulator
from core.predictor import get_all_predictions, get_prediction
from core.router import build_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)


# ── WebSocket менеджер подключений ───────────────────────────

class ConnectionManager:
    def __init__(self) -> None:
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.append(ws)
        log.info("WS client connected. Total: %d", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.remove(ws)
        log.info("WS client disconnected. Total: %d", len(self._clients))

    async def broadcast(self, data: dict) -> None:
        if not self._clients:
            return
        payload = json.dumps(data, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.remove(ws)


ws_manager   = ConnectionManager()
_tick_secs   = SIM_TICK_SECS    # мутабельная скорость


# ── Фоновая задача симуляции ─────────────────────────────────

async def _sim_loop() -> None:
    """
    Бесконечный цикл: каждые _tick_secs секунд делает тик симуляции
    и пушит снапшот всем WebSocket-клиентам.
    """
    global _tick_secs
    log.info("Симуляционный цикл запущен (%.1f сек/тик)", _tick_secs)
    while True:
        await asyncio.sleep(_tick_secs)
        await simulator.tick()

        states  = simulator.get_all_states()
        preds   = get_all_predictions()
        sim_st  = simulator.get_sim_status()

        payload = {
            "type":    "tick",
            "sim":     sim_st,
            "states":  states,
            "predictions": preds,
        }
        await ws_manager.broadcast(payload)


# ── Lifespan (startup / shutdown) ────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Загрузка данных...")
    simulator.load()
    log.info("Запуск симуляционного цикла...")
    task = asyncio.create_task(_sim_loop())
    yield
    task.cancel()
    log.info("Сервер остановлен.")


# ── FastAPI App ───────────────────────────────────────────────

app = FastAPI(
    title="ATM Monitor API",
    description="Real-time ATM cash monitoring with ML predictions",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статические файлы дашборда
dashboard_dir = BASE_DIR / "dashboard"
if dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")


# ═══════════════════════════════════════════════════════════
# REST ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "service": "ATM Monitor API",
        "docs":    "/docs",
        "dashboard": "/dashboard/index.html",
        "ws":      "ws://localhost:8000/ws/live",
    }


@app.get("/api/atms", summary="Все ATM — текущий баланс + ML-прогноз")
async def get_atms():
    states  = simulator.get_all_states()
    preds   = get_all_predictions()

    result = []
    for atm_id, st in states.items():
        ml = preds.get(atm_id, {})
        result.append({**st, "prediction": ml or None})

    result.sort(key=lambda x: x["balance_pct"])
    return {"atms": result, "count": len(result), "timestamp": simulator.get_current_ts()}


@app.get("/api/atms/{atm_id}", summary="Детали одного ATM")
async def get_atm(atm_id: str):
    state = simulator.get_state(atm_id)
    if not state:
        raise HTTPException(404, f"ATM {atm_id} не найден")

    history = simulator.get_history(atm_id, last_n=48)
    ml      = get_prediction(atm_id)

    return {
        "state":      state,
        "prediction": ml,
        "history":    history,
    }


@app.get("/api/atms/{atm_id}/history", summary="История баланса")
async def get_history(
    atm_id: str,
    last_n: int = Query(48, ge=1, le=360, description="Количество последних шагов"),
):
    state = simulator.get_state(atm_id)
    if not state:
        raise HTTPException(404, f"ATM {atm_id} не найден")

    return {
        "atm_id":  atm_id,
        "history": simulator.get_history(atm_id, last_n=last_n),
    }


@app.get("/api/predictions", summary="ML-прогнозы для всех ATM")
async def get_predictions():
    preds = get_all_predictions()
    return {"predictions": preds, "count": len(preds)}


@app.get("/api/alerts", summary="ATM с риском (critical + warning)")
async def get_alerts():
    alerts  = simulator.get_alerts()
    preds   = get_all_predictions()

    enriched = []
    for a in alerts:
        ml = preds.get(a["atm_id"], {})
        enriched.append({
            **a,
            "cashout_prob": ml.get("cashout_prob"),
            "risk_label":   ml.get("risk_label"),
        })

    return {"alerts": enriched, "count": len(enriched)}


# ── Маршруты инкассации ──────────────────────────────────────

class RouteRequest(BaseModel):
    filter_status: str = "warning"   # critical | warning | all
    num_cars:      int = 2
    speed_kmh:     float = 30.0


@app.post("/api/route", summary="Построить маршрут инкассации")
async def get_route(req: RouteRequest):
    if req.num_cars < 1 or req.num_cars > 4:
        raise HTTPException(400, "num_cars должен быть от 1 до 4")
    if req.filter_status not in ("critical", "warning", "all"):
        raise HTTPException(400, "filter_status: critical | warning | all")

    states = simulator.get_all_states()
    preds  = get_all_predictions()

    route = build_routes(
        atm_states=states,
        atm_predictions=preds,
        filter_status=req.filter_status,
        num_cars=req.num_cars,
        speed_kmh=req.speed_kmh,
    )
    return route


# ── Управление симуляцией ────────────────────────────────────

@app.get("/api/sim/status", summary="Статус симуляции")
async def sim_status():
    return simulator.get_sim_status()


class SpeedRequest(BaseModel):
    tick_secs: float   # реальных секунд на один 2-часовой тик


@app.post("/api/sim/speed", summary="Изменить скорость симуляции")
async def set_speed(req: SpeedRequest):
    global _tick_secs
    if not (0.2 <= req.tick_secs <= 30.0):
        raise HTTPException(400, "tick_secs: 0.2 — 30.0")
    _tick_secs = req.tick_secs
    speed_x = 2 * 3600 / req.tick_secs
    return {"tick_secs": _tick_secs, "speed_x": round(speed_x)}


# ═══════════════════════════════════════════════════════════
# WEBSOCKET
# ═══════════════════════════════════════════════════════════

@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    """
    Live-поток данных.
    Сервер пушит tick-сообщения каждые SIM_TICK_SECS.
    Клиент может слать: {"action": "route", ...}
    """
    await ws_manager.connect(ws)

    # Сразу отправляем текущий снапшот
    initial = {
        "type":        "snapshot",
        "sim":         simulator.get_sim_status(),
        "states":      simulator.get_all_states(),
        "predictions": get_all_predictions(),
        "meta":        simulator.get_all_meta(),
    }
    await ws.send_text(json.dumps(initial, ensure_ascii=False))

    try:
        while True:
            # Слушаем команды от клиента (неблокирующий receive)
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=0.1)
                cmd = json.loads(raw)
                await _handle_ws_command(ws, cmd)
            except asyncio.TimeoutError:
                pass   # нет сообщений — продолжаем ждать
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


async def _handle_ws_command(ws: WebSocket, cmd: dict) -> None:
    """Обрабатывает команды от WebSocket-клиента."""
    action = cmd.get("action")

    if action == "route":
        states = simulator.get_all_states()
        preds  = get_all_predictions()
        route  = build_routes(
            atm_states=states,
            atm_predictions=preds,
            filter_status=cmd.get("filter_status", "warning"),
            num_cars=cmd.get("num_cars", 2),
            speed_kmh=cmd.get("speed_kmh", 30.0),
        )
        await ws.send_text(json.dumps({"type": "route", "data": route}, ensure_ascii=False))

    elif action == "history":
        atm_id = cmd.get("atm_id")
        last_n = cmd.get("last_n", 48)
        history = simulator.get_history(atm_id, last_n=last_n)
        await ws.send_text(json.dumps({
            "type": "history", "atm_id": atm_id, "data": history
        }, ensure_ascii=False))

    elif action == "ping":
        await ws.send_text(json.dumps({"type": "pong"}))


# ═══════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )

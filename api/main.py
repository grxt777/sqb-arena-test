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
from core.predictor import get_all_predictions, get_prediction, refresh_predictions
from core.router import build_routes
from core.cassettes import CassetteSet, baseline_refill, MAX_BALANCE

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

        # Live ML-инференс по текущему состоянию симулятора
        await asyncio.get_event_loop().run_in_executor(
            None, refresh_predictions, simulator
        )

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


@app.get("/tashkent_districts.geojson", summary="Границы районов Ташкента (GeoJSON)")
async def get_tashkent_districts_geojson():
    geo_path = BASE_DIR / "tashkent_districts.geojson"
    if not geo_path.exists():
        raise HTTPException(404, "tashkent_districts.geojson не найден")
    return FileResponse(str(geo_path), media_type="application/geo+json")


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


@app.get("/api/atms/{atm_id}/cassettes", summary="Детальный разбор кассет + baseline банка")
async def get_cassettes(atm_id: str):
    state = simulator.get_state(atm_id)
    if not state:
        raise HTTPException(404, f"ATM {atm_id} не найден")

    # История за последние 84 записи = 1 неделя (84 × 2ч = 168ч)
    history = simulator.get_history(atm_id, last_n=84)
    last_week_outcome = sum(r.get("totalOutcome", 0) for r in history)

    cs = CassetteSet.from_balance(state["balance"])
    baseline = baseline_refill(last_week_outcome, state["balance"], sample_steps=len(history))

    ml_pred = get_prediction(atm_id) or {}
    ml_needed = max(0, state["balance"] - ml_pred.get("pred_balance_24h", state["balance"]))

    return {
        "atm_id":          atm_id,
        "name":            state["name"],
        "current_balance": state["balance"],
        "cassettes":       cs.to_dict(),
        "baseline":        baseline,
        "ml_recommendation": {
            "method":         "ML (XGBoost) прогноз",
            "pred_balance_24h": ml_pred.get("pred_balance_24h"),
            "cashout_prob":     ml_pred.get("cashout_prob"),
            "risk_label":       ml_pred.get("risk_label"),
            "refill_urgency":   "СРОЧНО" if ml_pred.get("risk_label") == "HIGH"
                                else "ПЛАНОВО" if ml_pred.get("risk_label") == "MEDIUM"
                                else "НЕ НУЖНО",
            "recommended_amount": round(ml_needed / 1e6, 1),
            "comment": (
                f"По ML: через 24ч останется {(ml_pred.get('pred_balance_24h',0)/1e6):.0f} млн. "
                f"Риск: {ml_pred.get('risk_label','—')}. "
                f"Нужно довезти: {round(ml_needed/1e6,1)} млн."
            ),
        },
        "savings_vs_baseline": {
            "cash_saved": max(0, baseline["baseline_amount"] - ml_needed),
            "comment": (
                f"Старый метод по прошлой неделе планирует {baseline['baseline_amount']//1_000_000} млн на 24ч. "
                f"ML рекомендует {round(ml_needed/1e6,1)} млн. "
                f"Разница: {max(0, baseline['baseline_amount'] - ml_needed)//1_000_000} млн."
            )
        }
    }


@app.get("/api/baseline", summary="Сравнение ML vs метод банка по всем ATM")
async def get_baseline_comparison():
    """
    Показывает разницу между текущим методом банка
    (раз в неделю, фиксированная сумма) и ML-прогнозом.
    """
    states = simulator.get_all_states()
    preds  = get_all_predictions()

    total_bank_refill = 0
    total_ml_needed   = 0
    cashouts_bank     = 0
    cashouts_ml       = 0
    rows = []

    for atm_id, st in states.items():
        history = simulator.get_history(atm_id, last_n=84)
        last_week = sum(r.get("totalOutcome", 0) for r in history)
        baseline  = baseline_refill(last_week, st["balance"], sample_steps=len(history))
        ml        = preds.get(atm_id, {})

        if baseline["baseline_risk"] == "cash-out":
            cashouts_bank += 1
        if ml.get("risk_label") == "HIGH":
            cashouts_ml += 1

        total_bank_refill += baseline["baseline_amount"]
        ml_needed = max(0, st["balance"] - ml.get("pred_balance_24h", st["balance"]))
        total_ml_needed += ml_needed

        rows.append({
            "atm_id":        atm_id,
            "name":          st["name"],
            "balance":       st["balance"],
            "bank_refill":   baseline["baseline_amount"],
            "bank_last_week_outcome": baseline["last_week_outcome"],
            "bank_overload": baseline["baseline_overfill"],
            "ml_recommended_refill": ml_needed,
            "ml_saved_vs_bank": max(0, baseline["baseline_amount"] - ml_needed),
            "ml_risk":       ml.get("risk_label", "—"),
            "ml_cashout_prob": ml.get("cashout_prob", 0),
        })

    ml_cash_saved = max(0, total_bank_refill - total_ml_needed)

    return {
        "summary": {
            "total_atms":          len(states),
            "bank_planned_refill": total_bank_refill,
            "bank_frozen_cash":    total_bank_refill,
            "bank_cashout_risk":   cashouts_bank,
            "ml_cashout_risk":     cashouts_ml,
            "ml_recommended_refill": total_ml_needed,
            "ml_cash_saved":       ml_cash_saved,
            "efficiency_gain_pct": round(
                ml_cash_saved / max(total_bank_refill, 1) * 100, 1
            ),
        },
        "atms": sorted(rows, key=lambda x: x["ml_cashout_prob"], reverse=True),
    }


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


@app.get("/api/analytics", summary="Аналитика по ATM за период")
async def get_analytics(
    period: str = Query("24h", pattern="^(12h|24h|7d|30d)$"),
):
    period_steps = {
        "12h": 6,
        "24h": 12,
        "7d": 84,
        "30d": 360,
    }
    labels = {
        "12h": "12 часов",
        "24h": "24 часа",
        "7d": "7 дней",
        "30d": "1 месяц",
    }
    last_n = period_steps[period]
    states = simulator.get_all_states()
    preds = get_all_predictions()

    total_outcome = 0
    total_income = 0
    incassations = 0
    cashout_events = 0
    prevented_cashouts = 0
    avg_balance_pcts = []
    demand_rows = []
    risk_rows = []

    for atm_id, st in states.items():
        history = simulator.get_history(atm_id, last_n=last_n)
        if not history:
            continue

        outcome = sum(float(r.get("totalOutcome", 0) or 0) for r in history)
        income = sum(float(r.get("totalIncome", 0) or 0) for r in history)
        inc_count = sum(1 for r in history if r.get("is_incassation"))
        capacity = float(st.get("capacity", 1) or 1)
        balances = [float(r.get("totalBalance", 0) or 0) for r in history]
        balance_pcts = [b / capacity * 100 for b in balances]
        min_pct = min(balance_pcts) if balance_pcts else st.get("balance_pct", 0)
        avg_pct = sum(balance_pcts) / max(len(balance_pcts), 1)
        for idx, record in enumerate(history):
            if not record.get("is_incassation") or idx == 0:
                continue
            prev_balance = float(history[idx - 1].get("totalBalance", 0) or 0)
            prev_pct = prev_balance / capacity * 100
            if prev_pct < 25 or history[idx - 1].get("low_cash_alert"):
                prevented_cashouts += 1

        total_outcome += outcome
        total_income += income
        incassations += inc_count
        cashout_events += sum(1 for p in balance_pcts if p < 20)
        avg_balance_pcts.append(avg_pct)

        ml = preds.get(atm_id, {})
        demand_rows.append({
            "atm_id": atm_id,
            "name": st.get("name", atm_id),
            "outcome": round(outcome),
            "income": round(income),
            "net": round(income - outcome),
            "avg_balance_pct": round(avg_pct, 1),
            "min_balance_pct": round(min_pct, 1),
            "incassations": inc_count,
        })
        risk_rows.append({
            "atm_id": atm_id,
            "name": st.get("name", atm_id),
            "balance_pct": st.get("balance_pct", 0),
            "cashout_prob": ml.get("cashout_prob", 0),
            "risk_label": ml.get("risk_label", "LOW"),
        })

    demand_rows.sort(key=lambda x: x["outcome"], reverse=True)
    risk_rows.sort(key=lambda x: (x["risk_label"] == "HIGH", x["cashout_prob"]), reverse=True)

    return {
        "period": period,
        "label": labels[period],
        "summary": {
            "total_outcome": round(total_outcome),
            "total_income": round(total_income),
            "net_cash_flow": round(total_income - total_outcome),
            "incassations": incassations,
            "cashout_events": cashout_events,
            "prevented_cashouts": prevented_cashouts,
            "avg_balance_pct": round(sum(avg_balance_pcts) / max(len(avg_balance_pcts), 1), 1),
            "high_risk_atms": sum(1 for r in risk_rows if r["risk_label"] == "HIGH"),
            "medium_risk_atms": sum(1 for r in risk_rows if r["risk_label"] == "MEDIUM"),
        },
        "top_demand": demand_rows[:7],
        "top_risk": risk_rows[:7],
    }


@app.get("/api/transactions/history", summary="История транзакций ATM с пагинацией")
async def get_transaction_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=5, le=50),
    atm_id: Optional[str] = Query(None),
):
    states = simulator.get_all_states()
    if atm_id and atm_id not in states:
        raise HTTPException(404, f"ATM {atm_id} не найден")

    target_ids = [atm_id] if atm_id else list(states.keys())
    rows: list[dict[str, Any]] = []

    for current_atm_id in target_ids:
        state = states.get(current_atm_id, {})
        history = simulator.get_history(current_atm_id, last_n=96)
        for record in history:
            outcome = int(record.get("totalOutcome", 0) or 0)
            income = int(record.get("totalIncome", 0) or 0)
            balance = int(record.get("totalBalance", 0) or 0)
            capacity = int(state.get("capacity", MAX_BALANCE) or MAX_BALANCE)
            balance_pct = round(balance / max(capacity, 1) * 100, 1)

            if record.get("is_breakdown"):
                tx_type = "Техническое событие"
                amount = 0
                status = "Ошибка"
            elif record.get("is_incassation"):
                tx_type = "Инкассация"
                amount = income
                status = "Пополнение"
            elif outcome > 0:
                tx_type = "Снятие наличных"
                amount = outcome
                status = "Успешно"
            elif income > 0:
                tx_type = "Внесение наличных"
                amount = income
                status = "Успешно"
            else:
                tx_type = "Проверка баланса"
                amount = 0
                status = "Инфо"

            if record.get("low_cash_alert"):
                status = "Low cash"

            rows.append({
                "timestamp": record.get("transactionTime"),
                "atm_id": current_atm_id,
                "atm_name": state.get("name", current_atm_id),
                "type": tx_type,
                "amount": amount,
                "balance": balance,
                "balance_pct": balance_pct,
                "status": status,
            })

    rows.sort(key=lambda row: row.get("timestamp") or "", reverse=True)

    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    safe_page = min(page, total_pages)
    start = (safe_page - 1) * page_size
    end = start + page_size

    return {
        "page": safe_page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "items": rows[start:end],
        "summary": {
            "withdrawals": sum(1 for row in rows if row["type"] == "Снятие наличных"),
            "incassations": sum(1 for row in rows if row["type"] == "Инкассация"),
            "technical_events": sum(1 for row in rows if row["type"] == "Техническое событие"),
            "low_cash_events": sum(1 for row in rows if row["status"] == "Low cash"),
        },
    }


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

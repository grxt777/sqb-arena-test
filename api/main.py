"""
ATM Monitor — FastAPI Backend
==============================

REST endpoints:
  GET   /api/atms                        → список ATM из БД
  GET   /api/atms/{terminal_id}          → детали ATM
  GET   /api/atms/{terminal_id}/cassettes → кассеты + baseline банка
  POST  /api/atms/import                 → загрузка XLSX
  POST  /api/atms/import/clear           → очистить БД
  GET   /api/atms/stats                  → статистика по областям/филиалам
  POST  /api/atms/{terminal_id}/balance  → обновить баланс
  POST  /api/atms/balances/bulk          → массовое обновление
  GET   /api/alerts                      → ATM в critical/warning
  GET   /api/baseline                    → сравнение ML vs baseline

Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config import BASE_DIR
from core.db import (
    init_db, count_atms, list_atms, get_atm, list_regions, list_branches,
    truncate_atms, bulk_insert_atms, update_balance, bulk_update_balances,
)
from core.importer import parse_xlsx, REQUIRED_FIELDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)


# ── Lifespan ─────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Инициализация БД ATM...")
    init_db()
    n = count_atms()
    log.info("БД готова. ATM в базе: %d", n)
    yield
    log.info("Сервер остановлен.")


# ── FastAPI App ──────────────────────────────────────────────

app = FastAPI(
    title="ATM Monitor API",
    description="Управление реестром банкоматов, импорт XLSX, мониторинг остатков",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dashboard static
dashboard_dir = BASE_DIR / "dashboard"
if dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")


# ═══════════════════════════════════════════════════════════
# ROOT
# ═══════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "service": "ATM Monitor API",
        "version": "2.0.0",
        "docs": "/docs",
        "dashboard": "/dashboard/index.html",
        "atms_in_db": count_atms(),
    }


# ═══════════════════════════════════════════════════════════
# ATM — список / детали
# ═══════════════════════════════════════════════════════════

@app.get("/api/atms", summary="Список ATM (с фильтрами)")
async def get_atms(
    region: Optional[str] = Query(None, description="Область"),
    branch: Optional[str] = Query(None, description="Филиал"),
    status: Optional[str] = Query(None, pattern="^(ok|warning|critical|unknown)$"),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    rows = list_atms(region=region, branch=branch, status=status, limit=limit, offset=offset)
    return {
        "atms": rows,
        "count": len(rows),
        "total_in_db": count_atms(),
        "filters": {"region": region, "branch": branch, "status": status},
    }


@app.get("/api/atms/stats", summary="Статистика по областям/филиалам")
async def get_stats():
    return {
        "total": count_atms(),
        "by_region": list_regions(),
        "by_branch": list_branches(),
    }


@app.get("/api/atms/{terminal_id}", summary="Детали ATM по Terminal ID")
async def get_atm_detail(terminal_id: str):
    atm = get_atm(terminal_id)
    if not atm:
        raise HTTPException(404, f"ATM с terminal_id={terminal_id!r} не найден")
    return atm


# ═══════════════════════════════════════════════════════════
# КАССЕТЫ + BASELINE
# ═══════════════════════════════════════════════════════════

@app.get("/api/atms/{terminal_id}/cassettes", summary="Кассеты + рекомендация")
async def get_atm_cassettes(terminal_id: str):
    """
    Сейчас кассеты — логическая модель: текущий баланс разбивается
    на 4 номинала (10/50/100/200k UZS) пропорционально доле в обороте.
    """
    atm = get_atm(terminal_id)
    if not atm:
        raise HTTPException(404, f"ATM {terminal_id!r} не найден")

    balance = atm.get("balance")
    capacity = atm.get("capacity") or 400_000_000

    if balance is None:
        cassettes = {
            "denominations": [10_000, 50_000, 100_000, 200_000],
            "by_denom": {str(d): {"count": 0, "balance": 0, "fill_pct": 0}
                          for d in (10_000, 50_000, 100_000, 200_000)},
            "total_balance": 0,
            "total_fill_pct": 0,
            "value_to_fill": capacity,
        }
        return {
            "atm_id": terminal_id,
            "address": atm.get("address"),
            "region": atm.get("region"),
            "branch": atm.get("branch"),
            "current_balance": None,
            "capacity": capacity,
            "cassettes": cassettes,
            "comment": "Баланс не загружен. Обновите через /api/atms/{id}/balance.",
        }

    # Распределение по номиналам
    share = {10_000: 0.10, 50_000: 0.45, 100_000: 0.30, 200_000: 0.15}
    by_denom = {}
    for d, s in share.items():
        alloc = int(balance * s)
        count = alloc // d
        by_denom[str(d)] = {
            "count": int(count),
            "balance": int(count * d),
            "fill_pct": round(count * d / (d * (capacity * s / d)) * 100, 1) if s > 0 else 0,
        }

    total_cassette_value = sum(int(c["count"]) * int(d) for d, c in zip(
        [10_000, 50_000, 100_000, 200_000], by_denom.values()
    ))
    value_to_fill = max(0, capacity - balance)

    return {
        "atm_id": terminal_id,
        "address": atm.get("address"),
        "region": atm.get("region"),
        "branch": atm.get("branch"),
        "current_balance": balance,
        "capacity": capacity,
        "balance_pct": atm.get("balance_pct"),
        "status": atm.get("status"),
        "cassettes": {
            "denominations": [10_000, 50_000, 100_000, 200_000],
            "by_denom": by_denom,
            "total_balance": total_cassette_value,
            "total_fill_pct": round(balance / capacity * 100, 1) if capacity else 0,
            "value_to_fill": value_to_fill,
        },
        "refill_needed": value_to_fill,
    }


# ═══════════════════════════════════════════════════════════
# ИМПОРТ XLSX
# ═══════════════════════════════════════════════════════════

@app.post("/api/atms/import", summary="Импорт ATM из XLSX")
async def import_atms(
    file: UploadFile = File(..., description="XLSX с реестром ATM"),
    replace: bool = Query(
        False,
        description="Если True — сначала очистить таблицу, потом импортировать",
    ),
):
    """
    Ожидаемая структура XLSX:
      A — Номер банкомата
      B — Номер по филиалам
      C — Локал код
      D — Область
      E — Филиал
      F — Модель банкомата
      G — Тармоқ тизими тури
      H — Серия рақами
      I — Мерчант ID
      J — Терминал ID
      K — Адрес
      L — Геолокация (lat)
      M — Геолокация (lon)
    """
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Ожидается .xlsx файл")

    # Сохраняем во временный файл
    tmp_dir = tempfile.mkdtemp(prefix="atm_import_")
    tmp_path = os.path.join(tmp_dir, file.filename)
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        try:
            parsed = parse_xlsx(tmp_path)
        except ValueError as e:
            raise HTTPException(400, str(e))

        if replace:
            deleted = truncate_atms()
        else:
            deleted = 0

        stats = bulk_insert_atms(parsed["records"])

        return {
            "ok": True,
            "filename": file.filename,
            "header_row": parsed["header_row"],
            "columns_detected": parsed["columns"],
            "total_rows_in_file": parsed["total_rows"],
            "deleted_before_import": deleted,
            "imported": stats["inserted"],
            "updated": stats["updated"],
            "skipped_no_terminal_id": stats["skipped"],
            "validation_errors": parsed["errors"][:50],
            "validation_errors_count": len(parsed["errors"]),
            "atms_in_db_after": count_atms(),
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/api/atms/import/clear", summary="Очистить все ATM из БД")
async def clear_atms():
    deleted = truncate_atms()
    return {"ok": True, "deleted": deleted, "atms_in_db": count_atms()}


# ═══════════════════════════════════════════════════════════
# БАЛАНСЫ
# ═══════════════════════════════════════════════════════════

class BalanceUpdate(BaseModel):
    balance: int


class BulkBalances(BaseModel):
    updates: List[Dict[str, Any]]  # [{"terminal_id": "...", "balance": 123}, ...]


@app.post("/api/atms/{terminal_id}/balance", summary="Обновить баланс ATM")
async def set_balance(terminal_id: str, payload: BalanceUpdate):
    if payload.balance < 0:
        raise HTTPException(400, "balance должен быть >= 0")
    ok = update_balance(terminal_id, payload.balance)
    if not ok:
        raise HTTPException(404, f"ATM {terminal_id!r} не найден")
    atm = get_atm(terminal_id)
    return {"ok": True, "atm": atm}


@app.post("/api/atms/balances/bulk", summary="Массовое обновление балансов")
async def set_balances_bulk(payload: BulkBalances):
    stats = bulk_update_balances(payload.updates)
    return {"ok": True, **stats}


# ═══════════════════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════════════════

@app.get("/api/alerts", summary="ATM в critical/warning")
async def get_alerts(limit: int = Query(200, ge=1, le=2000)):
    all_atms = list_atms(limit=limit)
    alerts = [a for a in all_atms if a.get("status") in ("critical", "warning")]
    alerts.sort(key=lambda x: (x.get("balance_pct") or 999))
    return {"alerts": alerts, "count": len(alerts)}


# ═══════════════════════════════════════════════════════════
# BASELINE
# ═══════════════════════════════════════════════════════════

@app.get("/api/baseline", summary="Сводный отчёт по остаткам")
async def get_baseline():
    """
    Упрощённый baseline без ML:
    - low_cash (critical + warning) — потенциальные cash-out
    - healthy — ATM в норме
    - no_data — баланс ещё не загружен
    """
    all_atms = list_atms(limit=5000)
    total_capacity = sum(a.get("capacity") or 0 for a in all_atms)
    total_balance = sum(a.get("balance") or 0 for a in all_atms)
    no_data = sum(1 for a in all_atms if a.get("balance") is None)
    critical = sum(1 for a in all_atms if a.get("status") == "critical")
    warning  = sum(1 for a in all_atms if a.get("status") == "warning")
    ok       = sum(1 for a in all_atms if a.get("status") == "ok")
    fill_pct = (total_balance / total_capacity * 100) if total_capacity else 0

    return {
        "summary": {
            "total_atms": len(all_atms),
            "total_capacity": total_capacity,
            "total_balance": total_balance,
            "fill_pct": round(fill_pct, 1),
            "no_data": no_data,
            "critical": critical,
            "warning": warning,
            "ok": ok,
        },
        "by_region": _aggregate_by_region(all_atms),
    }


def _aggregate_by_region(atms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    agg: Dict[str, Dict[str, Any]] = {}
    for a in atms:
        r = a.get("region") or "Не указана"
        bucket = agg.setdefault(r, {
            "region": r,
            "count": 0,
            "total_capacity": 0,
            "total_balance": 0,
            "critical": 0,
            "warning": 0,
            "ok": 0,
            "no_data": 0,
        })
        bucket["count"] += 1
        bucket["total_capacity"] += a.get("capacity") or 0
        bucket["total_balance"] += a.get("balance") or 0
        st = a.get("status")
        if st == "critical": bucket["critical"] += 1
        elif st == "warning": bucket["warning"] += 1
        elif st == "ok":      bucket["ok"] += 1
        else:                 bucket["no_data"] += 1
    out = []
    for r in agg.values():
        cap = r["total_capacity"] or 1
        r["fill_pct"] = round(r["total_balance"] / cap * 100, 1)
        out.append(r)
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


# ═══════════════════════════════════════════════════════════
# GEOJSON
# ═══════════════════════════════════════════════════════════

@app.get("/tashkent_districts.geojson", summary="Границы районов Ташкента")
async def get_tashkent_geojson():
    geo_path = BASE_DIR / "tashkent_districts.geojson"
    if not geo_path.exists():
        raise HTTPException(404, "GeoJSON не найден")
    return FileResponse(str(geo_path), media_type="application/geo+json")


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

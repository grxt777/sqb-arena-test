"""
Движок симуляции — воспроизводит исторические данные как live-поток.
Singleton: один экземпляр на весь процесс FastAPI.

Принцип работы:
  - Загружает CSV один раз при старте
  - Хранит текущий timeIndex
  - tick() → сдвигает индекс, обновляет state всех ATM
  - get_state(atm_id) → мгновенный снапшот без блокировок
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from .config import (
    CSV_PATH, SIM_START_DATE, SIM_STEP_HOURS,
    LOW_CASH_PCT, WARNING_CASH_PCT
)

log = logging.getLogger(__name__)


class ATMSimulator:
    """
    Thread-safe (asyncio) симулятор исторических данных ATM.
    """

    def __init__(self) -> None:
        self._data:      Dict[str, List[dict]] = {}   # atmId → sorted records
        self._meta:      Dict[str, dict]        = {}   # atmId → static info
        self._time_steps: List[str]             = []   # отсортированные метки
        self._index:     int                    = 0
        self._state:     Dict[str, dict]        = {}   # текущий снапшот
        self._running:   bool                   = False
        self._lock:      asyncio.Lock           = asyncio.Lock()
        self._loaded:    bool                   = False

    # ── Загрузка ─────────────────────────────────────────────

    def load(self) -> None:
        """Загружает и индексирует CSV. Вызывается один раз при старте."""
        if self._loaded:
            return

        log.info("Загрузка датасета: %s", CSV_PATH)
        df = pd.read_csv(
            CSV_PATH,
            parse_dates=["transactionTime"],
            usecols=[
                "atmId", "atmName", "atmBank", "atmProfile",
                "atmAddress", "lat", "lon", "atm_capacity",
                "transactionTime", "totalBalance",
                "totalOutcome", "totalIncome",
                "is_incassation", "is_breakdown", "low_cash_alert",
                "cash_utilization_pct",
            ],
        )

        # Фильтруем с даты старта симуляции
        df = df[df["transactionTime"] >= SIM_START_DATE].copy()
        df = df.sort_values(["atmId", "transactionTime"])
        log.info("Строк после фильтрации: %d", len(df))

        # Строим индекс
        grouped = defaultdict(list)
        times   = set()
        for row in df.itertuples(index=False):
            rec = {
                "transactionTime":  str(row.transactionTime),
                "totalBalance":     int(row.totalBalance),
                "totalOutcome":     int(row.totalOutcome),
                "totalIncome":      int(row.totalIncome),
                "is_incassation":   int(row.is_incassation),
                "is_breakdown":     int(row.is_breakdown),
                "low_cash_alert":   int(row.low_cash_alert),
                "cash_utilization_pct": float(row.cash_utilization_pct),
            }
            grouped[row.atmId].append(rec)
            times.add(str(row.transactionTime))

            if row.atmId not in self._meta:
                self._meta[row.atmId] = {
                    "atm_id":   row.atmId,
                    "name":     row.atmName,
                    "bank":     row.atmBank,
                    "profile":  row.atmProfile,
                    "address":  row.atmAddress,
                    "lat":      float(row.lat),
                    "lon":      float(row.lon),
                    "capacity": int(row.atm_capacity),
                }

        self._data       = dict(grouped)
        self._time_steps = sorted(times)
        self._loaded     = True

        # Найдём стартовый индекс
        start = SIM_START_DATE
        for i, ts in enumerate(self._time_steps):
            if ts >= start:
                self._index = i
                break

        self._snapshot()
        log.info("Симулятор готов. ATM: %d, шагов: %d", len(self._meta), len(self._time_steps))

    # ── Тик ──────────────────────────────────────────────────

    def _snapshot(self) -> None:
        """Обновляет _state по текущему _index (без блокировки)."""
        if not self._time_steps:
            return

        ts = self._time_steps[self._index]

        for atm_id, records in self._data.items():
            # Берём запись с совпадающей временной меткой
            # Используем _index как позицию (данные отсортированы одинаково)
            pos = min(self._index, len(records) - 1)
            row = records[pos]
            meta = self._meta[atm_id]
            cap  = meta["capacity"]
            bal  = row["totalBalance"]
            pct  = bal / cap if cap else 0

            if pct < LOW_CASH_PCT:
                status = "critical"
            elif pct < WARNING_CASH_PCT:
                status = "warning"
            else:
                status = "ok"

            # Последняя инкассация
            last_inc = None
            for r in reversed(records[:pos + 1]):
                if r["is_incassation"]:
                    last_inc = r["transactionTime"][:10]
                    break

            # Скорость расхода (последние 6 периодов = 12 часов)
            recent = records[max(0, pos - 5): pos + 1]
            avg_out = sum(r["totalOutcome"] for r in recent) / max(len(recent), 1)
            hours_to_empty = (bal / avg_out * SIM_STEP_HOURS) if avg_out > 0 else None

            self._state[atm_id] = {
                "atm_id":           atm_id,
                "name":             meta["name"],
                "bank":             meta["bank"],
                "lat":              meta["lat"],
                "lon":              meta["lon"],
                "capacity":         cap,
                "balance":          bal,
                "balance_pct":      round(pct * 100, 1),
                "status":           status,
                "is_incassation":   bool(row["is_incassation"]),
                "is_breakdown":     bool(row["is_breakdown"]),
                "last_incassation": last_inc,
                "hours_to_empty":   round(hours_to_empty, 1) if hours_to_empty else None,
                "timestamp":        ts,
            }

    async def tick(self) -> str:
        """Один шаг симуляции. Возвращает текущую временную метку."""
        async with self._lock:
            self._index = (self._index + 1) % len(self._time_steps)
            self._snapshot()
            return self._time_steps[self._index]

    # ── Геттеры ───────────────────────────────────────────────

    def get_all_states(self) -> Dict[str, dict]:
        return dict(self._state)

    def get_state(self, atm_id: str) -> Optional[dict]:
        return self._state.get(atm_id)

    def get_meta(self, atm_id: str) -> Optional[dict]:
        return self._meta.get(atm_id)

    def get_all_meta(self) -> Dict[str, dict]:
        return dict(self._meta)

    def get_history(self, atm_id: str, last_n: int = 48) -> List[dict]:
        records = self._data.get(atm_id, [])
        end = min(self._index + 1, len(records))
        start = max(0, end - last_n)
        return records[start:end]

    def get_current_ts(self) -> str:
        return self._time_steps[self._index] if self._time_steps else ""

    def get_sim_status(self) -> dict:
        return {
            "current_timestamp": self.get_current_ts(),
            "step_index":        self._index,
            "total_steps":       len(self._time_steps),
            "speed_x":           SIM_STEP_HOURS * 3600 / 2.0,   # во сколько раз быстрее
        }

    def get_alerts(self, min_prob: float = 0.35) -> List[dict]:
        """Возвращает ATM в критическом состоянии или с высоким ML-риском."""
        alerts = []
        for atm_id, st in self._state.items():
            if st["status"] in ("critical", "warning"):
                alerts.append({
                    "atm_id":         atm_id,
                    "name":           st["name"],
                    "balance_pct":    st["balance_pct"],
                    "status":         st["status"],
                    "hours_to_empty": st.get("hours_to_empty"),
                })
        alerts.sort(key=lambda x: x["balance_pct"])
        return alerts


# ── Singleton ─────────────────────────────────────────────────
simulator = ATMSimulator()

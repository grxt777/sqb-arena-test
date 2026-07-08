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
import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from datetime import datetime, timedelta

from .config import (
    CSV_PATH, SIM_START_DATE, SIM_STEP_HOURS,
    LOW_CASH_PCT, WARNING_CASH_PCT
)
from .calendar_uz import is_salary_day, is_near_salary
from .cassettes import CassetteSet, MAX_BALANCE, CASSETTE_VALUE, NUM_CASSETTES

log = logging.getLogger(__name__)

# Кастомные публичные названия ATM (без брендов), согласованные с пользователем.
# Ключ: atm_id (atm001 ... atm045)
ATM_CUSTOM_NAMES: Dict[str, str] = {
    "atm001": "Банкомат на Юнусабад 14-й квартал",
    "atm002": "Банкомат на улице Фаол",
    "atm003": "Банкомат на Юнусабадском базаре",
    "atm004": "Банкомат возле ТЦ Мегапланет",
    "atm005": "Банкомат на Казы Махалля",
    "atm006": "Банкомат в Универсаме",
    "atm007": "Банкомат возле метро Юнусабад",
    "atm008": "Банкомат возле метро Бадамзар",
    "atm009": "Банкомат на улице Богишамол",
    "atm010": "Банкомат на улице Ахилобод",
    "atm011": "Банкомат на улице Биллур",
    "atm012": "Банкомат возле Юнусабадского кольцевого круга",
    "atm013": "Банкомат возле Центра плова Бешказан",
    "atm014": "Банкомат возле гостиницы Кинг Плаза",
    "atm015": "Банкомат на улице Шахристан",
    "atm016": "Банкомат на улице Лабзак",
    "atm017": "Банкомат возле метро Шахристан",
    "atm018": "Банкомат возле супермаркета на 14 квартале Юнусабадского района",
    "atm019": "Банкомат возле мечети Мирза Юсуф, Бадамзар Махалля",
    "atm020": "Банкомат возле мечети Минор",
    "atm021": "Банкомат у ТЦ Атлас, 19-й квартал",
    "atm022": "Банкомат возле гостиницы Интерконтиненталь",
    "atm023": "Банкомат на 15-м квартале Махалля Юнусота",
    "atm024": "Банкомат возле гостиницы Навруз",
    "atm025": "Банкомат на Махалля Узбекистон Мустакиллиги",
    "atm026": "Банкомат возле гостиницы Radisson Blu Hotel Tashkent",
    "atm027": "Банкомат возле почтового центра на Бадамзаре",
    "atm028": "Банкомат возле Института фармацевтического образования и исследований",
    "atm029": "Банкомат возле продуктового магазина 19-й квартал",
    "atm030": "Банкомат 13-й квартал, фудкорт MaxWay",
    "atm031": "Банкомат на рынке Малика, Малая кольцевая дорога, 59",
    "atm032": "Банкомат у Проспекта Амира Темура, 7Б/1",
    "atm033": "Банкомат 7-й квартал, возле продуктового магазина",
    "atm034": "Банкомат на Малой кольцевой дороге",
    "atm035": "Банкомат на Махалле Увайсий",
    "atm036": "Банкомат возле посольства Вьетнама",
    "atm037": "Банкомат на улице Тахтапуль",
    "atm038": "Банкомат Юнусабад 18-й квартал, 54",
    "atm039": "Банкомат Юнусабад 6-й квартал, 46",
    "atm040": "Банкомат на Махалле Туркистан",
    "atm041": "Банкомат на Махалле Хусниабад",
    "atm042": "Банкомат возле Юнусабадского рынка",
    "atm043": "Банкомат в ТЦ Кефаят",
    "atm044": "Банкомат в Махалле Юнусабад",
    "atm045": "Банкомат возле перекрёстка Янги Юнусабад",
}

ATM_CUSTOM_ADDRESSES: Dict[str, str] = {
    atm_id: name.replace("Банкомат ", "", 1) if name.startswith("Банкомат ") else name
    for atm_id, name in ATM_CUSTOM_NAMES.items()
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками в км."""
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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
        self._real_atms: List[dict]             = []

    def _load_real_atms(self) -> None:
        """Загружает реальные ATM/банки из real_atms.json."""
        real_path = Path(CSV_PATH).resolve().parent / "real_atms.json"
        if not real_path.exists():
            log.warning("real_atms.json не найден: %s", real_path)
            self._real_atms = []
            return

        try:
            raw = json.loads(real_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Не удалось прочитать real_atms.json: %s", exc)
            self._real_atms = []
            return

        points: List[dict] = []
        for item in raw:
            try:
                points.append(
                    {
                        "name": str(item.get("name") or "").strip(),
                        "bank": str(item.get("bank") or "").strip(),
                        "address": str(item.get("address") or "").strip(),
                        "lat": float(item["lat"]),
                        "lon": float(item["lon"]),
                    }
                )
            except Exception:
                continue
        self._real_atms = points
        log.info("Реальные ATM/банки загружены: %d", len(self._real_atms))

    def _match_nearest_real_atm(self, lat: float, lon: float) -> Optional[dict]:
        """Находит ближайший реальный ATM/банк по координатам."""
        if not self._real_atms:
            return None
        nearest = min(
            self._real_atms,
            key=lambda x: _haversine_km(lat, lon, x["lat"], x["lon"]),
        )
        return nearest

    @staticmethod
    def _best_real_label(real_item: Optional[dict]) -> str:
        """Возвращает лучшее текстовое имя из real_atms (без Unknown)."""
        if not real_item:
            return ""
        name = str(real_item.get("name") or "").strip()
        bank = str(real_item.get("bank") or "").strip()
        if name and name.lower() != "unknown" and name.lower() != "atm":
            return name
        if bank and bank.lower() != "unknown":
            return bank
        if name and name.lower() != "unknown":
            return name
        return ""

    @staticmethod
    def _is_unknown_text(value: str) -> bool:
        v = (value or "").strip().lower()
        return v in {"", "unknown", "atm"} or v.startswith("unknown")

    @staticmethod
    def _anonymized_atm_name(
        atm_id: str,
        address: str,
        lat: float,
        lon: float,
    ) -> str:
        """
        Возвращает нейтральное имя банкомата:
        - по адресу, если он есть
        - иначе по координатам.
        """
        addr = (address or "").strip()
        if addr and not ATMSimulator._is_unknown_text(addr):
            return f"Банкомат ({addr})"
        return f"Банкомат {atm_id.upper()} [{lat:.5f}, {lon:.5f}]"

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
        self._load_real_atms()

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
                nearest = self._match_nearest_real_atm(float(row.lat), float(row.lon))
                src_name = str(row.atmName or "").strip()
                src_bank = str(row.atmBank or "").strip()
                src_addr = str(row.atmAddress or "").strip()
                is_unknown_name = src_name.lower().startswith("unknown")
                is_unknown_bank = src_bank.lower() in {"", "unknown"}
                is_unknown_addr = src_addr.lower() in {"", "unknown"} or src_addr.lower().startswith("unknown")

                # Юридически безопасный режим:
                # - никаких брендовых названий банков/ATM
                # - банк всегда "Банк"
                # - название ATM зависит только от адреса (или координат)
                nearest_label = self._best_real_label(nearest)
                name = (
                    nearest_label or nearest["name"]
                    if nearest and (is_unknown_name or not src_name)
                    else src_name
                )
                bank = (
                    nearest_label or nearest["bank"] or nearest["name"]
                    if nearest and (is_unknown_bank or not src_bank)
                    else src_bank
                )
                address = (
                    nearest["address"] or nearest["name"] or nearest["bank"]
                    if nearest and is_unknown_addr
                    else src_addr
                )
                safe_name = self._anonymized_atm_name(
                    atm_id=row.atmId,
                    address=address or "",
                    lat=float(row.lat),
                    lon=float(row.lon),
                )
                final_name = ATM_CUSTOM_NAMES.get(row.atmId, safe_name)

                final_address = ATM_CUSTOM_ADDRESSES.get(
                    row.atmId,
                    (address or src_addr or "").strip() or f"Юнусабадский район ({row.atmId.upper()})",
                )

                self._meta[row.atmId] = {
                    "atm_id":   row.atmId,
                    "name":     final_name,
                    "bank":     "Банк",
                    "profile":  row.atmProfile,
                    "address":  final_address,
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

            # ── Скорость расхода (последние 6 периодов = 12 часов) ──
            recent = records[max(0, pos - 5): pos + 1]
            avg_out = sum(r["totalOutcome"] for r in recent) / max(len(recent), 1)
            hours_to_empty = (bal / avg_out * SIM_STEP_HOURS) if avg_out > 0 else None

            # ── Конкретное время опустошения ─────────────────────
            empty_at: Optional[str] = None
            if hours_to_empty is not None:
                try:
                    cur_dt = datetime.fromisoformat(ts)
                    empty_dt = cur_dt + timedelta(hours=hours_to_empty)
                    empty_at = empty_dt.strftime("%d %b %H:%M")
                except Exception:
                    empty_at = None

            # ── Зарплатный день (единый calendar_uz) ─────────────
            try:
                cur_dt_obj = datetime.fromisoformat(ts)
                _is_sal  = is_salary_day(cur_dt_obj)
                _is_near = is_near_salary(cur_dt_obj)
            except Exception:
                _is_sal = _is_near = False

            # ── Реальные 4 кассеты (4 номинала) ─────────────────
            cs = CassetteSet.from_balance(bal)
            cs_dict = cs.to_dict()
            cash_ok_12h = (hours_to_empty is None) or (hours_to_empty > 12)

            self._state[atm_id] = {
                "atm_id":             atm_id,
                "name":               meta["name"],
                "bank":               meta["bank"],
                "address":            meta.get("address", ""),
                "profile":            meta.get("profile", "residential"),
                "lat":                meta["lat"],
                "lon":                meta["lon"],
                "capacity":           cap,
                "balance":            bal,
                "balance_pct":        round(pct * 100, 1),
                "status":             status,
                "is_incassation":     bool(row["is_incassation"]),
                "is_breakdown":       bool(row["is_breakdown"]),
                "last_incassation":   last_inc,
                "hours_to_empty":     round(hours_to_empty, 1) if hours_to_empty else None,
                "empty_at":           empty_at,
                "is_salary_day":      _is_sal,
                "is_near_salary":     _is_near,
                # 4 кассеты
                "cassettes":          cs_dict["cassettes"],
                "cassettes_total_fill_pct": cs_dict["total_fill_pct"],
                "cassettes_value_to_fill":  cs_dict["value_to_fill"],
                "cassettes_bills_to_fill":  cs_dict["bills_to_fill"],
                "cash_ok_12h":        cash_ok_12h,
                "timestamp":          ts,
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

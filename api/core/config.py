"""
Централизованная конфигурация — единый источник истины.
Все пути, параметры симуляции и ML берутся отсюда.
"""

from pathlib import Path

# ── Пути ────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parents[2]   # ATM/
DATA_DIR   = BASE_DIR
MODELS_DIR = BASE_DIR / "models"
PRED_DIR   = BASE_DIR / "predictions"

CSV_PATH  = DATA_DIR / "atm_transactions_enriched.csv"
PRED_PATH = PRED_DIR / "atm_predictions.csv"

# ── Симуляция ────────────────────────────────────────────────
SIM_STEP_HOURS   = 2        # шаг симуляции в часах
SIM_TICK_SECS    = 2.0      # реальных секунд на один тик (скорость воспроизведения)
SIM_START_DATE   = "2024-10-01"   # стартовая дата воспроизведения

# ── Бизнес-пороги ────────────────────────────────────────────
LOW_CASH_PCT      = 0.20    # критичный уровень
WARNING_CASH_PCT  = 0.40    # уровень предупреждения

# ── ML ───────────────────────────────────────────────────────
CASHOUT_THRESHOLD = 0.35    # порог классификатора (prob >= → риск)
HORIZON_HOURS     = 24

# ── Инкассация ───────────────────────────────────────────────
DEPOT = {"lat": 41.3510, "lon": 69.2830, "name": "Депо (Amir Temur 107)"}
ROAD_FACTOR = 1.35          # поправка прямое расстояние → дорога
AVG_SPEED_KMH = 30

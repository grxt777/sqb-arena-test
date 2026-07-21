"""
Экспортирует лёгкие JSON-файлы для дашборда вместо полного CSV.
Запускать после generate_atm_dataset.py и train_model.py.

Создаёт:
  dashboard/data/timeseries.json  — последние 90 дней по каждому ATM
  dashboard/data/predictions.json — ML-прогнозы
  dashboard/data/meta.json        — метаданные ATM
"""

import json
import pandas as pd
from pathlib import Path

OUT_DIR = Path("C:/Users/gruto/OneDrive/Desktop/ATM/dashboard/data")
OUT_DIR.mkdir(exist_ok=True)

CSV_PATH  = "C:/Users/gruto/OneDrive/Desktop/ATM/atm_transactions_enriched.csv"
PRED_PATH = "C:/Users/gruto/OneDrive/Desktop/ATM/predictions/atm_predictions.csv"

DAYS_BACK = 30   # сколько дней истории брать

print("Читаем CSV...")
df = pd.read_csv(CSV_PATH, parse_dates=["transactionTime"])
df = df.sort_values(["atmId", "transactionTime"])

cutoff = df["transactionTime"].max() - pd.Timedelta(days=DAYS_BACK)
df = df[df["transactionTime"] >= cutoff]
print(f"  Строк за последние {DAYS_BACK} дней: {len(df):,}")

# ── 1. META — статические данные по каждому ATM ──────────────
meta_cols = ["atmId", "atmName", "atmBank", "atmProfile",
             "atmAddress", "lat", "lon", "atm_capacity"]
meta = (df[meta_cols].drop_duplicates("atmId")
                      .set_index("atmId")
                      .to_dict(orient="index"))

with open(OUT_DIR / "meta.json", "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False)
print(f"  meta.json: {len(meta)} ATM")

# ── 2. TIMESERIES — история баланса для графиков ─────────────
ts_cols = [
    "atmId", "transactionTime",
    "totalBalance", "atm_capacity",
    "totalOutcome", "is_incassation",
    "low_cash_alert",
]
ts_cols = [c for c in ts_cols if c in df.columns]

timeseries = {}
for atm_id, grp in df[ts_cols].groupby("atmId"):
    grp = grp.copy()
    grp["transactionTime"] = grp["transactionTime"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    timeseries[atm_id] = grp.drop(columns="atmId").to_dict(orient="records")

with open(OUT_DIR / "timeseries.json", "w", encoding="utf-8") as f:
    json.dump(timeseries, f, ensure_ascii=False, separators=(",", ":"))

size_kb = (OUT_DIR / "timeseries.json").stat().st_size / 1024
print(f"  timeseries.json: {size_kb:.0f} KB ({DAYS_BACK} дней)")

# ── 3. PREDICTIONS — ML-прогнозы ─────────────────────────────
pred_cols = [
    "atmId", "totalBalance", "cash_utilization_pct",
    "pred_balance_24h", "pred_balance_pct_24h",
    "pred_cashout_prob", "pred_cashout_risk", "risk_label",
    "transactionTime",
]
pred_df = pd.read_csv(PRED_PATH)
pred_cols_exist = [c for c in pred_cols if c in pred_df.columns]
preds = pred_df[pred_cols_exist].set_index("atmId").to_dict(orient="index")

with open(OUT_DIR / "predictions.json", "w", encoding="utf-8") as f:
    json.dump(preds, f, ensure_ascii=False, separators=(",", ":"))
print(f"  predictions.json: {len(preds)} ATM")

print("\nГотово! Файлы в dashboard/data/")

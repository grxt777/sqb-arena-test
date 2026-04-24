"""
ML-инференс — загружает обученные XGBoost модели и выдаёт прогнозы.
Кэшируется на уровне процесса: модели загружаются один раз.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import MODELS_DIR, PRED_PATH, CASHOUT_THRESHOLD

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_models():
    """Загружает модели один раз, кэширует в памяти."""
    import joblib

    reg_path = MODELS_DIR / "xgb_regressor.joblib"
    clf_path = MODELS_DIR / "xgb_classifier.joblib"
    feat_path = MODELS_DIR / "feature_names.json"

    if not all(p.exists() for p in [reg_path, clf_path, feat_path]):
        log.warning("Модели не найдены в %s — инференс недоступен", MODELS_DIR)
        return None, None, None

    reg   = joblib.load(reg_path)
    clf   = joblib.load(clf_path)
    feats = json.loads(feat_path.read_text())
    log.info("Модели загружены. Признаков: %d", len(feats))
    return reg, clf, feats


@lru_cache(maxsize=1)
def _load_static_predictions() -> Dict[str, dict]:
    """
    Загружает предрасчитанные прогнозы из CSV.
    Используется как fallback когда нет онлайн-данных для инференса.
    """
    if not PRED_PATH.exists():
        return {}

    df = pd.read_csv(PRED_PATH)
    result = {}
    for _, row in df.iterrows():
        result[row["atmId"]] = {
            "atm_id":           row["atmId"],
            "pred_balance_24h": float(row.get("pred_balance_24h", 0)),
            "pred_balance_pct": float(row.get("pred_balance_pct_24h", 0)),
            "cashout_prob":     float(row.get("pred_cashout_prob", 0)),
            "cashout_risk":     bool(row.get("pred_cashout_risk", False)),
            "risk_label":       str(row.get("risk_label", "LOW")),
        }
    log.info("Статические прогнозы загружены: %d ATM", len(result))
    return result


def get_prediction(atm_id: str) -> Optional[dict]:
    """Возвращает ML-прогноз для одного ATM (статический из CSV)."""
    preds = _load_static_predictions()
    return preds.get(atm_id)


def get_all_predictions() -> Dict[str, dict]:
    """Возвращает прогнозы для всех ATM."""
    return dict(_load_static_predictions())


def risk_label(prob: float) -> str:
    if prob >= 0.65: return "HIGH"
    if prob >= CASHOUT_THRESHOLD: return "MEDIUM"
    return "LOW"

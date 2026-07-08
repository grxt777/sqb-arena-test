"""
ML-инференс — live-прогноз на основе текущего состояния симулятора.

Каждый тик: refresh_predictions(simulator) →
  - берёт последние 48 записей по каждому ATM
  - строит фичи через features.make_features() — ТОТЖЕ КОД что train_model.py
  - прогоняет XGBoost reg+clf
  - обновляет кэш

Fallback: если модели не найдены — статический CSV.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .config import MODELS_DIR, PRED_PATH, CASHOUT_THRESHOLD
from .features import make_features

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_models():
    import joblib

    reg_path  = MODELS_DIR / "xgb_regressor.joblib"
    clf_path  = MODELS_DIR / "xgb_classifier.joblib"
    feat_path = MODELS_DIR / "feature_names.json"

    if not all(p.exists() for p in [reg_path, clf_path, feat_path]):
        log.warning("Модели не найдены в %s — live-инференс недоступен", MODELS_DIR)
        return None, None, None

    reg   = joblib.load(reg_path)
    clf   = joblib.load(clf_path)
    feats = json.loads(feat_path.read_text())
    log.info("XGBoost модели загружены. Признаков: %d", len(feats))
    return reg, clf, feats


@lru_cache(maxsize=1)
def _load_static_predictions() -> Dict[str, dict]:
    """Fallback: статические прогнозы из CSV."""
    if not PRED_PATH.exists():
        return {}
    df = pd.read_csv(PRED_PATH)
    result = {}
    for _, row in df.iterrows():
        result[str(row["atmId"])] = {
            "atm_id":           str(row["atmId"]),
            "pred_balance_24h": float(row.get("pred_balance_24h", 0)),
            "pred_balance_pct": float(row.get("pred_balance_pct_24h", 0)),
            "cashout_prob":     float(row.get("pred_cashout_prob", 0)),
            "cashout_risk":     bool(row.get("pred_cashout_risk", False)),
            "risk_label":       str(row.get("risk_label", "LOW")),
        }
    log.info("Статические прогнозы (fallback) загружены: %d ATM", len(result))
    return result


# ── Live кэш ─────────────────────────────────────────────────
_live_predictions: Dict[str, dict] = {}
_profile_enc: Dict[str, int] = {}
_id_enc:      Dict[str, int] = {}


def _ensure_encoders(meta_dict: dict) -> None:
    global _profile_enc, _id_enc
    if _profile_enc:
        return
    profiles = sorted({v.get("profile", "residential") for v in meta_dict.values()})
    ids      = sorted(meta_dict.keys())
    _profile_enc = {p: i for i, p in enumerate(profiles)}
    _id_enc      = {a: i for i, a in enumerate(ids)}


def _risk_label(prob: float) -> str:
    if prob >= 0.65:
        return "HIGH"
    if prob >= CASHOUT_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def refresh_predictions(simulator) -> None:
    """
    Пересчитывает live-прогнозы по текущим данным симулятора.
    Вызывается после каждого тика.
    """
    global _live_predictions

    reg, clf, feats = _load_models()
    if reg is None:
        _live_predictions = dict(_load_static_predictions())
        return

    meta_all = simulator.get_all_meta()
    _ensure_encoders(meta_all)

    results: Dict[str, dict] = {}
    for atm_id, meta in meta_all.items():
        records = simulator.get_history(atm_id, last_n=48)
        if not records:
            continue

        try:
            df = pd.DataFrame(records)
            # Добавляем заглушки для колонок которых нет в истории симулятора
            for col in ["numberIncomeTransaction", "numberOutcomeTransaction", "totalNumberTransaction"]:
                if col not in df.columns:
                    df[col] = 0

            df = make_features(
                df,
                capacity=meta.get("capacity", 1),
                profile_enc=_profile_enc.get(meta.get("profile", "residential"), 0),
                atm_id_enc=_id_enc.get(atm_id, 0),
            )

            # Последняя строка — текущее состояние
            for f in feats:
                if f not in df.columns:
                    df[f] = 0
            row = df[feats].iloc[[-1]].fillna(0)

            pred_bal     = float(reg.predict(row)[0])
            cashout_prob = float(clf.predict_proba(row)[0][1])

        except Exception as exc:
            log.debug("Инференс %s: %s", atm_id, exc)
            continue

        cap = meta.get("capacity", 1) or 1
        pred_pct = pred_bal / cap * 100

        results[atm_id] = {
            "atm_id":           atm_id,
            "pred_balance_24h": round(pred_bal),
            "pred_balance_pct": round(pred_pct, 1),
            "cashout_prob":     round(cashout_prob, 4),
            "cashout_risk":     cashout_prob >= CASHOUT_THRESHOLD,
            "risk_label":       _risk_label(cashout_prob),
        }

    if results:
        _live_predictions = results
        log.debug("Live-прогнозы: %d ATM", len(results))
    elif not _live_predictions:
        _live_predictions = dict(_load_static_predictions())


def get_prediction(atm_id: str) -> Optional[dict]:
    return _live_predictions.get(atm_id)


def get_all_predictions() -> Dict[str, dict]:
    if not _live_predictions:
        return dict(_load_static_predictions())
    return dict(_live_predictions)

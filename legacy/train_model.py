import os, sys
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")

"""
ATM Cash-Out Prediction Pipeline
=================================
Архитектура:
  - Task A (Regression):      предсказать totalBalance через 24 часа
  - Task B (Classification):  вероятность cash-out (balance < 20%) в следующие 24 часа

Feature Engineering:
  - Лаги баланса и снятий (1, 3, 6, 12, 24 периода = 2, 6, 12, 24, 48 часов)
  - Rolling statistics (mean, std, min за 12 и 24 периода)
  - Time-to-empty (расчётное время до 0 по текущей скорости расхода)
  - Признаки локации (профиль ATM, ёмкость)
  - Временные признаки (hour, dow, month, is_salary_day, is_holiday, ...)

Разбивка данных:
  - Train: 2023-01-01 — 2024-09-30  (~75%)
  - Val:   2024-10-01 — 2024-11-30  (~12.5%)
  - Test:  2024-12-01 — 2024-12-31  (~12.5%)
  Временной сплит (no leakage).

Выходные файлы:
  - models/xgb_regressor.joblib
  - models/xgb_classifier.joblib
  - models/feature_names.json
  - predictions/atm_predictions.csv
  - reports/model_report.txt
"""

import json
import os
import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    roc_auc_score,
    average_precision_score,
    classification_report,
)
from sklearn.preprocessing import LabelEncoder
import xgboost as xgb

warnings.filterwarnings("ignore")

# Подключаем единый модуль календаря
sys.path.insert(0, str(Path(__file__).parent / "api"))
try:
    from core.calendar_uz import SALARY_DAYS, is_salary_day, is_near_salary
    print("[OK] calendar_uz: SALARY_DAYS =", sorted(SALARY_DAYS))
except ImportError:
    SALARY_DAYS = frozenset({10, 25})
    is_salary_day  = lambda d: d in SALARY_DAYS
    is_near_salary = lambda d: any(abs(d - s) <= 2 for s in SALARY_DAYS)
    print("[Fallback] SALARY_DAYS =", sorted(SALARY_DAYS))

# -
# КОНФИГУРАЦИЯ
# -

DATA_PATH   = Path("atm_transactions_enriched.csv")
MODELS_DIR  = Path("models");      MODELS_DIR.mkdir(exist_ok=True)
PRED_DIR    = Path("predictions"); PRED_DIR.mkdir(exist_ok=True)
REPORT_DIR  = Path("reports");     REPORT_DIR.mkdir(exist_ok=True)

STEP_HOURS      = 2        # шаг временного ряда
HORIZON_HOURS   = 24       # горизонт прогноза
HORIZON_STEPS   = HORIZON_HOURS // STEP_HOURS   # = 12 шагов вперёд

LOW_CASH_PCT    = 0.20     # порог cash-out

TRAIN_END = "2024-09-30"
VAL_END   = "2024-11-30"

XGB_REG_PARAMS = {
    "n_estimators":     800,
    "learning_rate":    0.05,
    "max_depth":        7,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "reg_alpha":        0.1,
    "reg_lambda":       1.0,
    "tree_method":      "hist",
    "device":           "cpu",
    "random_state":     42,
    "n_jobs":           -1,
}

XGB_CLF_PARAMS = {
    **XGB_REG_PARAMS,
    "scale_pos_weight": 4,   # компенсация дисбаланса классов
    "eval_metric":      "aucpr",
}


# -
# 1. ЗАГРУЗКА И БАЗОВАЯ ОЧИСТКА
# -

def load_data() -> pd.DataFrame:
    print("Загружаем данные...")
    df = pd.read_csv(DATA_PATH, parse_dates=["transactionTime"])
    df = df.sort_values(["atmId", "transactionTime"]).reset_index(drop=True)

    # убираем периоды сбоя из обучения (ATM не работал — данные не репрезентативны)
    df = df[df["is_breakdown"] == 0].copy()

    print(f"  Строк после фильтрации сбоев: {len(df):,}")
    print(f"  ATM: {df['atmId'].nunique()} | Период: {df['transactionTime'].min().date()} -> {df['transactionTime'].max().date()}")
    return df


# -
# 2. FEATURE ENGINEERING
# -

LAG_STEPS    = [1, 3, 6, 12, 24]        # лаги баланса и снятий
ROLL_WINDOWS = [6, 12, 24]              # окна rolling statistics


def make_features(df: pd.DataFrame) -> pd.DataFrame:
    print("Feature engineering...")

    dfs = []
    for atm_id, grp in df.groupby("atmId", sort=False):
        grp = grp.copy().reset_index(drop=True)

        bal = grp["totalBalance"].astype(float)
        out = grp["totalOutcome"].astype(float)
        cap = grp["atm_capacity"].astype(float)
        pct = bal / cap

        # - Лаги -
        for lag in LAG_STEPS:
            grp[f"bal_lag_{lag}"]     = bal.shift(lag)
            grp[f"out_lag_{lag}"]     = out.shift(lag)
            grp[f"pct_lag_{lag}"]     = pct.shift(lag)

        # - Rolling statistics -
        for w in ROLL_WINDOWS:
            grp[f"bal_roll_mean_{w}"] = bal.shift(1).rolling(w).mean()
            grp[f"bal_roll_std_{w}"]  = bal.shift(1).rolling(w).std()
            grp[f"bal_roll_min_{w}"]  = bal.shift(1).rolling(w).min()
            grp[f"out_roll_mean_{w}"] = out.shift(1).rolling(w).mean()
            grp[f"out_roll_sum_{w}"]  = out.shift(1).rolling(w).sum()

        # - Скорость расхода (за последние 6 периодов) -
        avg_burn = out.shift(1).rolling(6).mean().clip(lower=1)
        grp["burn_rate_6p"]    = avg_burn
        grp["time_to_empty"]   = (bal / avg_burn).clip(upper=200)   # в периодах
        # pct_change через .pct_change(6) — идентично predictor.py
        grp["pct_change_6p"]   = pct.pct_change(periods=6).fillna(0).clip(-1, 1)

        # - Инкассаций за последние N периодов -
        grp["inc_last_12p"]    = grp["is_incassation"].shift(1).rolling(12).sum()
        grp["inc_last_24p"]    = grp["is_incassation"].shift(1).rolling(24).sum()

        # - Целевые переменные -
        # TARGET A: баланс через HORIZON_STEPS периодов (регрессия)
        grp["target_balance_24h"] = bal.shift(-HORIZON_STEPS)
        grp["target_pct_24h"]     = pct.shift(-HORIZON_STEPS)

        # TARGET B: будет ли cash-out в течение HORIZON_STEPS периодов (классификация)
        # 1 = в любой из следующих 12 периодов баланс упадёт ниже 20%
        future_low = pd.concat(
            [pct.shift(-s) for s in range(1, HORIZON_STEPS + 1)], axis=1
        ).min(axis=1)
        grp["target_cashout_24h"] = (future_low < LOW_CASH_PCT).astype(int)

        dfs.append(grp)

    result = pd.concat(dfs, ignore_index=True)
    print(f"  Фичей создано: {len(result.columns)}")
    return result


def encode_categoricals(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    le_map = {}
    for col in ["atmProfile", "atmId"]:
        if col in df.columns:
            le = LabelEncoder()
            df[col + "_enc"] = le.fit_transform(df[col].astype(str))
            le_map[col] = le
    return df, le_map


def get_feature_cols(df: pd.DataFrame) -> list[str]:
    """Возвращает список фич для модели (исключает целевые и мета-колонки)."""
    exclude = {
        "atmId", "atmName", "atmBank", "atmCity", "atmDistrict",
        "atmAddress", "atmProfile", "lat", "lon", "transactionTime",
        "holiday_name", "day_of_week_name",
        "target_balance_24h", "target_pct_24h", "target_cashout_24h",
        "is_breakdown", "is_incassation",
        "totalBalance", "prev_balance", "balance_change",
        "low_cash_alert", "cash_utilization_pct",
    }
    return [c for c in df.columns if c not in exclude and df[c].dtype in [np.float64, np.int64, np.float32, np.int32]]


# -
# 3. TEMPORAL SPLIT
# -

def temporal_split(df: pd.DataFrame):
    train = df[df["transactionTime"] <= TRAIN_END].copy()
    val   = df[(df["transactionTime"] > TRAIN_END) & (df["transactionTime"] <= VAL_END)].copy()
    test  = df[df["transactionTime"] > VAL_END].copy()

    print(f"\nРазбивка данных:")
    print(f"  Train: {len(train):>8,} строк  ({train['transactionTime'].min().date()} -> {train['transactionTime'].max().date()})")
    print(f"  Val:   {len(val):>8,} строк  ({val['transactionTime'].min().date()} -> {val['transactionTime'].max().date()})")
    print(f"  Test:  {len(test):>8,} строк  ({test['transactionTime'].min().date()} -> {test['transactionTime'].max().date()})")
    return train, val, test


# -
# 4. ОБУЧЕНИЕ
# -

def train_regressor(train, val, feature_cols):
    print("\n- Task A: Regression (balance_24h) -")

    mask_tr = train["target_balance_24h"].notna()
    mask_va = val["target_balance_24h"].notna()

    X_tr = train.loc[mask_tr, feature_cols].fillna(0)
    y_tr = train.loc[mask_tr, "target_balance_24h"]
    X_va = val.loc[mask_va, feature_cols].fillna(0)
    y_va = val.loc[mask_va, "target_balance_24h"]

    cap_tr = train.loc[mask_tr, "atm_capacity"]
    cap_va = val.loc[mask_va, "atm_capacity"]

    model = xgb.XGBRegressor(**XGB_REG_PARAMS)
    model.fit(
        X_tr, y_tr,
        eval_set=[(X_va, y_va)],
        verbose=100,
    )

    pred_va = model.predict(X_va)

    mae  = mean_absolute_error(y_va, pred_va)
    rmse = np.sqrt(mean_squared_error(y_va, pred_va))
    # MAPE по % от ёмкости
    mape_pct = np.mean(np.abs((y_va.values - pred_va) / cap_va.values)) * 100

    print(f"\n  Val MAE:       {mae:>15,.0f} сум")
    print(f"  Val RMSE:      {rmse:>15,.0f} сум")
    print(f"  Val MAPE(%):   {mape_pct:>14.2f}%  (ошибка в % от ёмкости)")

    return model, {"mae": mae, "rmse": rmse, "mape_pct": mape_pct}


def train_classifier(train, val, feature_cols):
    print("\n- Task B: Classification (cash_out_risk_24h) -")

    mask_tr = train["target_cashout_24h"].notna()
    mask_va = val["target_cashout_24h"].notna()

    X_tr = train.loc[mask_tr, feature_cols].fillna(0)
    y_tr = train.loc[mask_tr, "target_cashout_24h"].astype(int)
    X_va = val.loc[mask_va, feature_cols].fillna(0)
    y_va = val.loc[mask_va, "target_cashout_24h"].astype(int)

    pos_rate = y_tr.mean()
    print(f"  Class balance — cash-out: {pos_rate:.1%}")

    model = xgb.XGBClassifier(**XGB_CLF_PARAMS)
    model.fit(
        X_tr, y_tr,
        eval_set=[(X_va, y_va)],
        verbose=100,
    )

    prob_va = model.predict_proba(X_va)[:, 1]
    pred_va = (prob_va >= 0.35).astype(int)   # порог 0.35 — bias к recall

    roc  = roc_auc_score(y_va, prob_va)
    aucpr = average_precision_score(y_va, prob_va)

    print(f"\n  Val ROC-AUC:   {roc:.4f}")
    print(f"  Val AUC-PR:    {aucpr:.4f}  (важнее при дисбалансе)")
    print(f"\n  Classification Report (threshold=0.35):")
    print(classification_report(y_va, pred_va, target_names=["no_risk", "cash_out_risk"], digits=3))

    return model, {"roc_auc": roc, "auc_pr": aucpr}


# -
# 5. FEATURE IMPORTANCE
# -

def print_feature_importance(model, feature_cols: list[str], top_n: int = 15, label: str = ""):
    imp = pd.Series(model.feature_importances_, index=feature_cols)
    top = imp.nlargest(top_n)
    print(f"\n  Top-{top_n} признаков ({label}):")
    for feat, score in top.items():
        bar = "█" * int(score * 300)
        print(f"    {feat:<35} {score:.4f}  {bar}")


# -
# 6. ФИНАЛЬНАЯ ОЦЕНКА НА ТЕСТЕ
# -

def evaluate_on_test(reg_model, clf_model, test, feature_cols):
    print("\n- Финальная оценка на TEST -")

    mask = test["target_balance_24h"].notna() & test["target_cashout_24h"].notna()
    X_te = test.loc[mask, feature_cols].fillna(0)
    y_te_reg = test.loc[mask, "target_balance_24h"]
    y_te_clf = test.loc[mask, "target_cashout_24h"].astype(int)
    cap_te   = test.loc[mask, "atm_capacity"]

    pred_reg  = reg_model.predict(X_te)
    prob_clf  = clf_model.predict_proba(X_te)[:, 1]
    pred_clf  = (prob_clf >= 0.35).astype(int)

    mae  = mean_absolute_error(y_te_reg, pred_reg)
    rmse = np.sqrt(mean_squared_error(y_te_reg, pred_reg))
    mape = np.mean(np.abs((y_te_reg.values - pred_reg) / cap_te.values)) * 100
    roc  = roc_auc_score(y_te_clf, prob_clf)
    aucpr = average_precision_score(y_te_clf, prob_clf)

    print(f"  [Regression]  MAE: {mae:>12,.0f} сум  |  RMSE: {rmse:>12,.0f} сум  |  MAPE: {mape:.2f}%")
    print(f"  [Classifier]  ROC-AUC: {roc:.4f}  |  AUC-PR: {aucpr:.4f}")
    print(f"\n  Classification Report (test, threshold=0.35):")
    print(classification_report(y_te_clf, pred_clf, target_names=["no_risk", "cash_out_risk"], digits=3))

    return {
        "test_mae": mae, "test_rmse": rmse, "test_mape_pct": mape,
        "test_roc_auc": roc, "test_auc_pr": aucpr,
    }


# -
# 7. ГЕНЕРАЦИЯ ПРЕДИКШЕНОВ ДЛЯ ВСЕХ ATM (последние данные)
# -

def generate_predictions(df: pd.DataFrame, reg_model, clf_model, feature_cols: list[str]) -> pd.DataFrame:
    """
    Для каждого ATM берём последнюю запись (актуальное состояние)
    и предсказываем баланс и риск через 24 часа.
    """
    print("\nГенерация предикшенов для дашборда...")

    last_rows = (
        df.sort_values("transactionTime")
          .groupby("atmId")
          .last()
          .reset_index()
    )

    X = last_rows[feature_cols].fillna(0)

    last_rows["pred_balance_24h"]    = reg_model.predict(X).clip(min=0)
    last_rows["pred_cashout_prob"]   = clf_model.predict_proba(X)[:, 1]
    last_rows["pred_cashout_risk"]   = (last_rows["pred_cashout_prob"] >= 0.35).astype(int)
    last_rows["pred_balance_pct_24h"] = (
        last_rows["pred_balance_24h"] / last_rows["atm_capacity"] * 100
    ).round(1)

    # Метка риска для дашборда
    def risk_label(prob):
        if prob >= 0.65: return "HIGH"
        if prob >= 0.35: return "MEDIUM"
        return "LOW"

    last_rows["risk_label"] = last_rows["pred_cashout_prob"].apply(risk_label)

    cols = [
        "atmId", "atmName", "atmBank", "atmProfile",
        "lat", "lon", "atm_capacity",
        "totalBalance", "cash_utilization_pct",
        "pred_balance_24h", "pred_balance_pct_24h",
        "pred_cashout_prob", "pred_cashout_risk", "risk_label",
        "transactionTime",
    ]
    result = last_rows[[c for c in cols if c in last_rows.columns]]

    out_path = PRED_DIR / "atm_predictions.csv"
    result.to_csv(out_path, index=False)
    print(f"  Сохранено: {out_path}  ({len(result)} ATM)")

    print(f"\n  Распределение рисков:")
    print(result["risk_label"].value_counts().to_string())

    return result


# -
# 8. СОХРАНЕНИЕ АРТЕФАКТОВ
# -

def save_artifacts(reg_model, clf_model, feature_cols, metrics: dict):
    joblib.dump(reg_model, MODELS_DIR / "xgb_regressor.joblib")
    joblib.dump(clf_model, MODELS_DIR / "xgb_classifier.joblib")

    with open(MODELS_DIR / "feature_names.json", "w") as f:
        json.dump(feature_cols, f, indent=2)

    report_lines = [
        "ATM Cash-Out Prediction — Model Report",
        "=" * 50,
        f"Horizon:      {HORIZON_HOURS}h ({HORIZON_STEPS} steps)",
        f"Cash-out thr: {LOW_CASH_PCT:.0%}",
        f"Train end:    {TRAIN_END}",
        f"Val end:      {VAL_END}",
        "",
        "- Validation -",
        f"  Regression  MAE:     {metrics.get('mae', 0):>15,.0f} сум",
        f"  Regression  RMSE:    {metrics.get('rmse', 0):>15,.0f} сум",
        f"  Regression  MAPE:    {metrics.get('mape_pct', 0):>14.2f}%",
        f"  Classifier  ROC-AUC: {metrics.get('roc_auc', 0):.4f}",
        f"  Classifier  AUC-PR:  {metrics.get('auc_pr', 0):.4f}",
        "",
        "- Test -",
        f"  Regression  MAE:     {metrics.get('test_mae', 0):>15,.0f} сум",
        f"  Regression  RMSE:    {metrics.get('test_rmse', 0):>15,.0f} сум",
        f"  Regression  MAPE:    {metrics.get('test_mape_pct', 0):>14.2f}%",
        f"  Classifier  ROC-AUC: {metrics.get('test_roc_auc', 0):.4f}",
        f"  Classifier  AUC-PR:  {metrics.get('test_auc_pr', 0):.4f}",
    ]

    report_path = REPORT_DIR / "model_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print(f"\n  Модели:  {MODELS_DIR}/")
    print(f"  Репорт:  {report_path}")


# -
# MAIN
# -

def main():
    print("=" * 60)
    print("  ATM Cash-Out Prediction Pipeline")
    print("=" * 60)

    # 1. Загрузка
    df = load_data()

    # 2. Feature engineering
    df = make_features(df)
    df, le_map = encode_categoricals(df)
    feature_cols = get_feature_cols(df)
    print(f"  Итого признаков: {len(feature_cols)}")

    # 3. Сплит
    train, val, test = temporal_split(df)

    # Убираем строки где целевые NaN (последние HORIZON_STEPS строк каждого ATM)
    train = train.dropna(subset=["target_balance_24h", "target_cashout_24h"])
    val   = val.dropna(subset=["target_balance_24h", "target_cashout_24h"])

    # 4. Обучение
    reg_model, reg_metrics = train_regressor(train, val, feature_cols)
    clf_model, clf_metrics = train_classifier(train, val, feature_cols)

    # 5. Feature importance
    print_feature_importance(reg_model, feature_cols, label="Regressor")
    print_feature_importance(clf_model, feature_cols, label="Classifier")

    # 6. Тест
    test_metrics = evaluate_on_test(reg_model, clf_model, test, feature_cols)

    all_metrics = {**reg_metrics, **clf_metrics, **test_metrics}

    # 7. Предикшены для дашборда
    df_full = df.dropna(subset=feature_cols[:5])   # убираем строки с NaN в ключевых фичах
    predictions = generate_predictions(df_full, reg_model, clf_model, feature_cols)

    # 8. Сохранение
    save_artifacts(reg_model, clf_model, feature_cols, all_metrics)

    print("\n" + "=" * 60)
    print("  Готово!")
    print("=" * 60)


if __name__ == "__main__":
    main()

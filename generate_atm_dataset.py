"""
ATM Dataset Generator
=====================
Генерирует обогащённый датасет транзакций банкоматов со следующими признаками:
- Исходные данные банкомата и транзакций
- Временные признаки (час, день недели, месяц и т.д.)
- Праздники и выходные Узбекистана
- Признаки инкассации и остатка наличных

Использование:
    python generate_atm_dataset.py

Результат: atm_transactions_enriched.csv
"""

import pandas as pd
import numpy as np
from datetime import datetime


# =============================================================================
# 1. КОНФИГУРАЦИЯ
# =============================================================================

# Список банкоматов Юнусабадского района Ташкента
# (atmId, atmName, atmBank, atmAddress, lat, lon, capacity)
ATM_LIST = [
    ("atm001", "Turon Telecom", "Turon Bank", "Turon Telecom", 41.3748229, 69.2988445, 35000000),
    ("atm002", "Unknown #2", "Unknown", "Unknown #2", 41.3331362, 69.2609006, 50000000),
    ("atm003", "Unknown #3", "Unknown", "Unknown #3", 41.3639912, 69.290124, 50000000),
    ("atm004", "Unknown #4", "Unknown", "Unknown #4", 41.3671007, 69.2910933, 50000000),
    ("atm005", "Unknown #5", "Unknown", "Unknown #5", 41.3339645, 69.3020418, 50000000),
    ("atm006", "Ipak Yo‘li Bank", "Ipak Yo‘li Bank", "Ipak Yo‘li Bank", 41.363548, 69.2887494, 50000000),
    ("atm007", "IpotekaBank", "Ipoteka Bank", "IpotekaBank", 41.3669084, 69.2911847, 55000000),
    ("atm008", "OFB", "Orient Finans Bank", "OFB", 41.3327745, 69.283731, 40000000),
    ("atm009", "Asia Alliance Bank", "Asia Alliance Bank", "Asia Alliance Bank", 41.3664184, 69.2864658, 45000000),
    ("atm010", "хамкор Банк #10", "хамкор Банк", "хамкор Банк #10", 41.3637761, 69.2976758, 50000000),
    ("atm011", "Юнусабадский центр банковских услуг #11", "Юнусабадский центр банковских услуг", "Юнусабадский центр банковских услуг #11", 41.3662339, 69.2858109, 50000000),
    ("atm012", "агробанк #12", "агробанк", "агробанк #12", 41.3658386, 69.2857591, 50000000),
    ("atm013", "Savdogarbank", "Savdogarbank", "Savdogarbank", 41.3474942, 69.286753, 35000000),
    ("atm014", "Юнусабадский центр банковских услуг #14", "Юнусабадский центр банковских услуг", "Юнусабадский центр банковских услуг #14", 41.3609751, 69.2777842, 50000000),
    ("atm015", "Шахристанский центр банковских услуг #15", "Шахристанский центр банковских услуг", "Шахристанский центр банковских услуг #15", 41.3534733, 69.2969781, 50000000),
    ("atm016", "IPAK YO'LI BANKI", "Ipak Yo'li Bank", "IPAK YO'LI BANKI", 41.3349194, 69.2650491, 60000000),
    ("atm017", "IPAK YO'LI BANKI", "Ipak Yo'li Bank", "IPAK YO'LI BANKI", 41.350856, 69.2883843, 60000000),
    ("atm018", "IPAK YO'LI BANKI", "Ipak Yo'li Bank", "IPAK YO'LI BANKI", 41.3744842, 69.2959416, 60000000),
    ("atm019", "IPAK YO'LI BANKI", "Ipak Yo'li Bank", "IPAK YO'LI BANKI", 41.3408644, 69.2933142, 60000000),
    ("atm020", "Ipak Yo'li Bank #20", "Ipak Yo'li Bank", "Ipak Yo'li Bank #20", 41.3381256, 69.2728383, 60000000),
    ("atm021", "Ipak Yo'li Bank #21", "Ipak Yo'li Bank", "Ipak Yo'li Bank #21", 41.3714788, 69.3111723, 60000000),
    ("atm022", "Ipak Yo'li Bank #22", "Ipak Yo'li Bank", "Ipak Yo'li Bank #22", 41.3373523, 69.2837333, 60000000),
    ("atm023", "Ipak Yo'li Bank #23", "Ipak Yo'li Bank", "Ipak Yo'li Bank #23", 41.3723644, 69.311564, 60000000),
    ("atm024", "Ipak Yo'li Bank #24", "Ipak Yo'li Bank", "Ipak Yo'li Bank #24", 41.3346616, 69.286201, 60000000),
    ("atm025", "IPAK YO'LI BANKI", "Ipak Yo'li Bank", "IPAK YO'LI BANKI", 41.3744698, 69.3050784, 60000000),
    ("atm026", "Unknown #26", "Unknown", "Unknown #26", 41.3345287, 69.2850718, 50000000),
    ("atm027", "Unknown #27", "Unknown", "Unknown #27", 41.3349718, 69.286142, 50000000),
    ("atm028", "Unknown #28", "Unknown", "Unknown #28", 41.3717123, 69.3118911, 50000000),
    ("atm029", "Unknown #29", "Unknown", "Unknown #29", 41.3718129, 69.3109311, 50000000),
    ("atm030", "Unknown #30", "Unknown", "Unknown #30", 41.3656856, 69.294607, 50000000),
    ("atm031", "Unknown #31", "Unknown", "Unknown #31", 41.3385807, 69.2720496, 50000000),
    ("atm032", "Unknown #32", "Unknown", "Unknown #32", 41.3626579, 69.2881187, 50000000),
    ("atm033", "IPAK YO'LI BANKI", "Ipak Yo'li Bank", "IPAK YO'LI BANKI", 41.3730126, 69.2727042, 60000000),
    ("atm034", "Ipak Yo'li Bank #34", "Ipak Yo'li Bank", "Ipak Yo'li Bank #34", 41.3413094, 69.2686272, 60000000),
    ("atm035", "Unknown #35", "Unknown", "Unknown #35", 41.3723966, 69.3116819, 50000000),
    ("atm036", "Kapitalbank", "Kapitalbank", "Kapitalbank", 41.331317, 69.2765674, 50000000),
    ("atm037", "Ipak Yo'li Bank #37", "Ipak Yo'li Bank", "Ipak Yo'li Bank #37", 41.3455986, 69.2612055, 60000000),
    ("atm038", "Ipak Yo'li Bank #38", "Ipak Yo'li Bank", "Ipak Yo'li Bank #38", 41.3714426, 69.3193745, 60000000),
    ("atm039", "Ipak Yo'li Bank #39", "Ipak Yo'li Bank", "Ipak Yo'li Bank #39", 41.3691277, 69.2659141, 60000000),
    ("atm040", "Ipak Yo'li Bank #40", "Ipak Yo'li Bank", "Ipak Yo'li Bank #40", 41.3636584, 69.2738816, 60000000),
    ("atm041", "Ipak Yo'li Bank #41", "Ipak Yo'li Bank", "Ipak Yo'li Bank #41", 41.3601826, 69.2804203, 60000000),
    ("atm042", "Гарант банк #42", "Гарант банк", "Гарант банк #42", 41.3636041, 69.2885533, 50000000),
    ("atm043", "Unknown #43", "Unknown", "Unknown #43", 41.3634269, 69.2875984, 50000000),
    ("atm044", "Orient Finans Bank (OFB)", "Orient Finans Bank", "Orient Finans Bank (OFB)", 41.361266, 69.2897211, 40000000),
    ("atm045", "AVO", "AVO", "AVO", 41.3716421, 69.3111385, 30000000),
]

# Диапазон дат симуляции
DATE_FROM = datetime(2023, 1, 1)
DATE_TO   = datetime(2024, 12, 31)

# Временной шаг (в часах)
TIME_STEP_HOURS = 2

# Начальный баланс и ёмкость банкомата (дефолт)
ATM_INITIAL_BALANCE = 40_000_000
ATM_CAPACITY        = 50_000_000

# Порог инкассации
LOW_CASH_THRESHOLD_PCT = 0.20

# =============================================================================
# ПРОФИЛИ БАНКОМАТОВ
# =============================================================================
# Каждый профиль задаёт:
#   hour_peaks  — пиковые часы (список диапазонов [start, end])
#   base_txn    — базовое кол-во транзакций за 2ч
#   weekend_factor — коэф. в выходные
#   salary_boost   — коэф. в зарплатный день
#   season_winter  — коэф. зимой (дек-фев)
#   season_summer  — коэф. летом (июн-авг)

ATM_PROFILES = {
    # Рядом с рынком/базаром — активен утром, слабее вечером
    "bozor": {
        "hour_peaks":     [(7, 12)],
        "base_txn":       25,
        "weekend_factor": 1.3,
        "salary_boost":   2.2,
        "season_winter":  0.85,
        "season_summer":  1.15,
    },
    # Метро / транзитная точка — два пика (утро+вечер)
    "metro": {
        "hour_peaks":     [(7, 9), (17, 20)],
        "base_txn":       30,
        "weekend_factor": 0.6,
        "salary_boost":   2.8,
        "season_winter":  0.9,
        "season_summer":  1.1,
    },
    # Офисный район — пик в обеденное время
    "office": {
        "hour_peaks":     [(12, 14), (17, 19)],
        "base_txn":       20,
        "weekend_factor": 0.3,
        "salary_boost":   3.0,
        "season_winter":  0.95,
        "season_summer":  0.9,
    },
    # Жилой квартал — равномерно, пик вечером
    "residential": {
        "hour_peaks":     [(17, 21)],
        "base_txn":       18,
        "weekend_factor": 1.1,
        "salary_boost":   2.5,
        "season_winter":  0.9,
        "season_summer":  1.05,
    },
    # Торговый центр — пик днём и выходные
    "mall": {
        "hour_peaks":     [(11, 20)],
        "base_txn":       22,
        "weekend_factor": 1.6,
        "salary_boost":   2.0,
        "season_winter":  1.1,
        "season_summer":  1.2,
    },
    # Банковский офис — рабочие часы
    "bank_branch": {
        "hour_peaks":     [(9, 17)],
        "base_txn":       15,
        "weekend_factor": 0.2,
        "salary_boost":   2.5,
        "season_winter":  1.0,
        "season_summer":  0.95,
    },
}

# Назначаем профили конкретным банкоматам (по id)
ATM_PROFILE_MAP = {
    "atm001": "mall",         # Turon Telecom — ТЦ
    "atm002": "residential",  # Unknown #2
    "atm003": "residential",  # Unknown #3
    "atm004": "office",       # Unknown #4
    "atm005": "residential",  # Unknown #5
    "atm006": "bank_branch",  # Ipak Yo'li Bank
    "atm007": "bank_branch",  # IpotekaBank
    "atm008": "bank_branch",  # OFB
    "atm009": "bank_branch",  # Asia Alliance Bank
    "atm010": "bozor",        # Hamkor Bank — рядом с рынком
    "atm011": "office",       # Yunusobod service center
    "atm012": "bank_branch",  # Agrobank
    "atm013": "bank_branch",  # Savdogarbank
    "atm014": "residential",  # service center
    "atm015": "residential",  # service center
    "atm016": "bank_branch",  # Ipak Yo'li Bank
    "atm017": "metro",        # Ipak Yo'li Bank — у метро
    "atm018": "mall",         # Ipak Yo'li Bank — ТЦ
    "atm019": "residential",  # Ipak Yo'li Bank
    "atm020": "residential",  # Ipak Yo'li Bank
    "atm021": "mall",         # Ipak Yo'li Bank — ТЦ
    "atm022": "bozor",        # Ipak Yo'li Bank — у рынка
    "atm023": "residential",  # Ipak Yo'li Bank
    "atm024": "residential",  # Ipak Yo'li Bank
    "atm025": "mall",         # Ipak Yo'li Bank — ТЦ
    "atm026": "residential",  # Unknown
    "atm027": "residential",  # Unknown
    "atm028": "residential",  # Unknown
    "atm029": "residential",  # Unknown
    "atm030": "office",       # Unknown
    "atm031": "residential",  # Unknown
    "atm032": "bozor",        # Unknown — у рынка
    "atm033": "bank_branch",  # Ipak Yo'li Bank
    "atm034": "residential",  # Ipak Yo'li Bank
    "atm035": "residential",  # Unknown
    "atm036": "bank_branch",  # Kapitalbank
    "atm037": "residential",  # Ipak Yo'li Bank
    "atm038": "residential",  # Ipak Yo'li Bank
    "atm039": "residential",  # Ipak Yo'li Bank
    "atm040": "bozor",        # Ipak Yo'li Bank — у рынка
    "atm041": "residential",  # Ipak Yo'li Bank
    "atm042": "bank_branch",  # Garant Bank
    "atm043": "office",       # Unknown
    "atm044": "bank_branch",  # Orient Finans Bank
    "atm045": "metro",        # AVO — транзитная точка
}

# Вероятность сбоя на одном 2-часовом периоде (ATM не работает)
BREAKDOWN_PROB  = 0.002   # ~0.2% на период ≈ ~1-2 сбоя в месяц на ATM
BREAKDOWN_HOURS = (2, 12)  # длительность сбоя — 2..12 часов


# =============================================================================
# 2. ПРАЗДНИКИ УЗБЕКИСТАНА (фиксированные + плавающие)
# =============================================================================

def get_uz_holidays(year: int) -> dict:
    """
    Возвращает словарь {date: holiday_name} для заданного года.
    Плавающие исламские праздники нужно уточнять ежегодно.
    """
    holidays = {
        # Фиксированные государственные праздники
        f"{year}-01-01": "Yangi yil (New Year)",
        f"{year}-01-02": "Yangi yil dam olish kuni",
        f"{year}-01-14": "Vatan himoyachilari kuni (Defenders' Day)",
        f"{year}-03-08": "Xotin-qizlar kuni (Women's Day)",
        f"{year}-03-21": "Navro'z bayrami (Nowruz)",
        f"{year}-03-22": "Navro'z bayrami (Nowruz)",
        f"{year}-03-23": "Navro'z bayrami (Nowruz)",
        f"{year}-05-09": "Xotira va qadrlash kuni (Memory Day)",
        f"{year}-09-01": "Mustaqillik kuni (Independence Day)",
        f"{year}-10-01": "O'qituvchilar kuni (Teachers' Day)",
        f"{year}-12-08": "Konstitutsiya kuni (Constitution Day)",
    }

    # Плавающие исламские праздники (приближённые даты — уточняйте каждый год)
    islamic_holidays = {
        2023: {
            "2023-04-21": "Ramazon Hayit (Eid al-Fitr)",
            "2023-04-22": "Ramazon Hayit (Eid al-Fitr)",
            "2023-06-28": "Qurbon Hayit (Eid al-Adha)",
            "2023-06-29": "Qurbon Hayit (Eid al-Adha)",
        },
        2024: {
            "2024-04-10": "Ramazon Hayit (Eid al-Fitr)",
            "2024-04-11": "Ramazon Hayit (Eid al-Fitr)",
            "2024-06-16": "Qurbon Hayit (Eid al-Adha)",
            "2024-06-17": "Qurbon Hayit (Eid al-Adha)",
        },
        2025: {
            "2025-03-30": "Ramazon Hayit (Eid al-Fitr)",
            "2025-03-31": "Ramazon Hayit (Eid al-Fitr)",
            "2025-06-06": "Qurbon Hayit (Eid al-Adha)",
            "2025-06-07": "Qurbon Hayit (Eid al-Adha)",
        },
    }

    if year in islamic_holidays:
        holidays.update(islamic_holidays[year])

    return {pd.Timestamp(k): v for k, v in holidays.items()}


def build_holiday_map(date_from: datetime, date_to: datetime) -> dict:
    """Собирает все праздники за диапазон лет."""
    holiday_map = {}
    for year in range(date_from.year, date_to.year + 1):
        holiday_map.update(get_uz_holidays(year))
    return holiday_map


# =============================================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

def get_hour_factor(hour: int, profile: dict) -> float:
    """Коэффициент активности по часу суток согласно профилю ATM."""
    # Ночной минимум
    if 0 <= hour < 6:
        return 0.1
    factor = 0.6  # базовый дневной
    for (start, end) in profile["hour_peaks"]:
        if start <= hour < end:
            factor = 1.8
            break
    return factor


def get_season_factor(month: int, profile: dict) -> float:
    if month in (12, 1, 2):
        return profile["season_winter"]
    if month in (6, 7, 8):
        return profile["season_summer"]
    return 1.0


def is_salary_day(day: int) -> bool:
    """10-е и 25-е — зарплатные дни в Узбекистане."""
    return day in (10, 25)


def is_near_salary(day: int) -> bool:
    """День до/после зарплатного дня тоже активнее."""
    return day in (9, 11, 24, 26)


# =============================================================================
# 4. ГЕНЕРАЦИЯ ВРЕМЕННОГО РЯДА ДЛЯ ОДНОГО БАНКОМАТА
# =============================================================================

def generate_atm_timeseries(
    atm_id: str,
    atm_name: str,
    atm_bank: str,
    atm_address: str,
    atm_lat: float,
    atm_lon: float,
    date_from: datetime,
    date_to: datetime,
    holiday_map: dict,
    step_hours: int = 2,
    initial_balance: int = ATM_INITIAL_BALANCE,
    capacity: int = ATM_CAPACITY,
    low_cash_pct: float = LOW_CASH_THRESHOLD_PCT,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Генерирует почасовые записи для одного банкомата.
    Учитывает: профиль локации, зарплатные дни, сезонность, сбои.
    """
    rng     = np.random.default_rng(seed)
    profile = ATM_PROFILES[ATM_PROFILE_MAP.get(atm_id, "residential")]

    timestamps = pd.date_range(date_from, date_to, freq=f"{step_hours}h")
    records    = []
    balance    = initial_balance
    prev_balance     = None
    breakdown_left   = 0   # сколько периодов ATM ещё «сломан»

    for ts in timestamps:
        date_only  = ts.normalize()
        hour       = ts.hour
        day        = ts.day
        month      = ts.month
        dow        = ts.dayofweek
        is_weekend = int(dow >= 5)
        is_holiday = int(date_only in holiday_map)
        holiday_name  = holiday_map.get(date_only, "")
        is_pre_holiday  = int((date_only + pd.Timedelta(days=1)) in holiday_map)
        is_post_holiday = int((date_only - pd.Timedelta(days=1)) in holiday_map)
        is_non_working  = int(is_weekend or is_holiday)
        is_salary       = int(is_salary_day(day))
        is_near_sal     = int(is_near_salary(day))

        # ── Случайный сбой ──────────────────────────────────────
        if breakdown_left > 0:
            breakdown_left -= 1
            is_breakdown = 1
        elif rng.random() < BREAKDOWN_PROB:
            breakdown_left = int(rng.integers(*BREAKDOWN_HOURS) // step_hours)
            is_breakdown = 1
        else:
            is_breakdown = 0

        # ── Факторы спроса ───────────────────────────────────────
        hour_factor    = get_hour_factor(hour, profile)
        season_factor  = get_season_factor(month, profile)
        day_factor     = profile["weekend_factor"] if is_non_working else 1.0
        salary_factor  = (profile["salary_boost"] if is_salary
                          else 1.4 if is_near_sal else 1.0)
        pre_hol_factor = 1.5 if is_pre_holiday else (1.2 if is_post_holiday else 1.0)

        combined = hour_factor * season_factor * day_factor * salary_factor * pre_hol_factor

        # ── Транзакции ───────────────────────────────────────────
        if is_breakdown:
            num_income_txn  = 0
            num_outcome_txn = 0
            total_income    = 0
            total_outcome   = 0
        else:
            base_lam = profile["base_txn"] * combined
            base_txn = int(rng.poisson(max(base_lam, 0.5)))

            # Депозиты (пополнения через ATM) — небольшая доля
            num_income_txn  = int(rng.poisson(max(2 * hour_factor, 0.1)))
            num_outcome_txn = max(0, base_txn - num_income_txn)

            # Суммы — в зарплатный день снимают больше за раз
            amt_scale = 1.6 if is_salary else 1.0
            total_income  = int(num_income_txn  * rng.integers(300_000, 2_000_000))
            total_outcome = int(num_outcome_txn * rng.integers(
                int(100_000 * amt_scale), int(600_000 * amt_scale)
            ))

        total_txn = num_income_txn + num_outcome_txn
        net_flow  = total_income - total_outcome

        # ── Инкассация ────────────────────────────────────────────
        is_incassation = 0
        if not is_breakdown and balance < capacity * low_cash_pct:
            refill = int(rng.integers(int(capacity * 0.6), int(capacity * 0.92)))
            balance        = min(balance + refill, capacity)
            is_incassation = 1
            total_income  += refill
            net_flow      += refill

        # ── Обновляем баланс ─────────────────────────────────────
        balance = max(0, balance + net_flow)
        balance = min(balance, capacity)

        balance_change   = (balance - prev_balance) if prev_balance is not None else 0
        low_cash_alert   = int(balance < capacity * low_cash_pct)
        utilization_pct  = round((capacity - balance) / capacity * 100, 2)
        atm_profile_name = ATM_PROFILE_MAP.get(atm_id, "residential")

        records.append({
            # Идентификаторы
            "atmId":       atm_id,
            "atmName":     atm_name,
            "atmBank":     atm_bank,
            "atmCity":     "Toshkent",
            "atmDistrict": "Yunusobod",
            "atmAddress":  atm_address,
            "atmProfile":  atm_profile_name,
            "lat":         atm_lat,
            "lon":         atm_lon,

            # Временная метка
            "transactionTime": ts,

            # Баланс и транзакции
            "totalBalance":             balance,
            "numberIncomeTransaction":  num_income_txn,
            "numberOutcomeTransaction": num_outcome_txn,
            "totalIncome":              total_income,
            "totalOutcome":             total_outcome,
            "totalNumberTransaction":   total_txn,
            "net_cash_flow":            net_flow,

            # Временные признаки
            "hour":             hour,
            "day_of_week":      dow,
            "day_of_week_name": ts.day_name(),
            "week_of_year":     ts.isocalendar().week,
            "month":            month,

            # Праздники и выходные
            "is_weekend":        is_weekend,
            "is_holiday":        is_holiday,
            "holiday_name":      holiday_name,
            "is_pre_holiday":    is_pre_holiday,
            "is_post_holiday":   is_post_holiday,
            "is_non_working_day": is_non_working,

            # Зарплата и сезонность
            "is_salary_day":     is_salary,
            "is_near_salary":    is_near_sal,
            "season_factor":     round(season_factor, 2),

            # Сбои
            "is_breakdown":      is_breakdown,

            # Инкассация
            "atm_capacity":    capacity,
            "prev_balance":    prev_balance if prev_balance is not None else balance,
            "balance_change":  balance_change,
            "is_incassation":  is_incassation,

            # Целевые
            "low_cash_alert":      low_cash_alert,
            "cash_utilization_pct": utilization_pct,
        })

        prev_balance = balance

    return pd.DataFrame(records)


# =============================================================================
# 4. ЗАПУСК
# =============================================================================

def main():
    print(f"Генерация датасета: {DATE_FROM.date()} -> {DATE_TO.date()}")
    print(f"Банкоматов: {len(ATM_LIST)}, шаг: {TIME_STEP_HOURS}ч\n")

    holiday_map = build_holiday_map(DATE_FROM, DATE_TO)
    print(f"Загружено праздников УЗ: {len(holiday_map)}")
    for d, name in sorted(holiday_map.items()):
        print(f"  {d.date()}  {name}")
    print()

    all_dfs = []
    for i, (atm_id, atm_name, atm_bank, atm_address, atm_lat, atm_lon, atm_cap) in enumerate(ATM_LIST):
        df_atm = generate_atm_timeseries(
            atm_id=atm_id,
            atm_name=atm_name,
            atm_bank=atm_bank,
            atm_address=atm_address,
            atm_lat=atm_lat,
            atm_lon=atm_lon,
            date_from=DATE_FROM,
            date_to=DATE_TO,
            holiday_map=holiday_map,
            step_hours=TIME_STEP_HOURS,
            initial_balance=int(atm_cap * 0.8),
            capacity=atm_cap,
            seed=42 + i,
        )
        all_dfs.append(df_atm)
        print(f"  [{i + 1}/{len(ATM_LIST)}] {atm_id}: {len(df_atm)} строк")

    df = pd.concat(all_dfs, ignore_index=True)

    output_path = "atm_transactions_enriched.csv"
    df.to_csv(output_path, index=False)

    print(f"\nГотово: {output_path}")
    print(f"Строк: {len(df):,} | Колонок: {len(df.columns)}")
    print(f"Колонки: {list(df.columns)}")


if __name__ == "__main__":
    main()

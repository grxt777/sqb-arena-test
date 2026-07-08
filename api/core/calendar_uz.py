"""
Единый модуль календаря для Узбекистана.
Импортируется везде: генератор, симулятор, предиктор, train_model.

Зарплатные дни: 10-е и 25-е число каждого месяца
(стандарт большинства гос. и частных предприятий Узбекистана).
"""

from __future__ import annotations
from datetime import date, datetime
from typing import Union

# ── Зарплатные дни ───────────────────────────────────────────
SALARY_DAYS: frozenset[int] = frozenset({10, 25})
NEAR_SALARY_DELTA: int = 2        # ±2 дня считается «около зарплаты»

# Праздники Узбекистана (месяц, день)
UZ_HOLIDAYS: frozenset[tuple[int, int]] = frozenset({
    (1,  1),   # Новый год
    (1,  2),   # Новый год (2-й день)
    (3,  8),   # День женщин
    (3,  21),  # Навруз
    (3,  22),  # Навруз
    (3,  23),  # Навруз
    (4,  30),  # День памяти и почестей
    (5,  1),   # День труда
    (5,  9),   # День памяти
    (6,  1),   # День защиты детей
    (8,  31),  # День независимости
    (9,  1),   # День независимости
    (10, 1),   # День учителя
    (12, 8),   # День Конституции
})


def is_salary_day(day: Union[int, date, datetime]) -> bool:
    d = day if isinstance(day, int) else (day.day if hasattr(day, 'day') else int(day))
    return d in SALARY_DAYS


def is_near_salary(day: Union[int, date, datetime]) -> bool:
    d = day if isinstance(day, int) else (day.day if hasattr(day, 'day') else int(day))
    return any(abs(d - s) <= NEAR_SALARY_DELTA for s in SALARY_DAYS)


def is_holiday(dt: Union[date, datetime]) -> bool:
    return (dt.month, dt.day) in UZ_HOLIDAYS


def season_factor(month: int) -> float:
    """Сезонный коэффициент спроса на наличные."""
    if month in (12, 1, 2):
        return 0.8    # зима — меньше снятий
    if month in (6, 7, 8):
        return 1.2    # лето — больше
    return 1.0

"""
Модель кассет банкомата.

Реальная структура (Uzbekistan):
  - 4 кассеты
  - Номиналы: 10 000, 50 000, 100 000, 200 000 UZS
  - Ёмкость каждой кассеты: 2 500 купюр (стандарт NCR/Wincor)

Логика пополнения:
  - Банк (по текущей практике): смотрит прошлые транзакции и планирует
    пополнение по историческому спросу.
  - Наша модель: прогнозирует будущую потребность и риск cash-out.

Baseline (метод банка):
  - Смотрим снятие за прошлую неделю.
  - Для сравнения с ML +24ч берём среднее снятие за день.
  - Результат сравнения: насколько ML снижает плановую загрузку наличных.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ── Конфигурация номиналов ────────────────────────────────────
DENOMINATIONS = [10_000, 50_000, 100_000, 200_000]   # UZS, от мелкого к крупному
NUM_CASSETTES = 4

# Ёмкость каждой кассеты (купюр) — подобрана так чтобы итого = 400 млн
# 10к×4000 + 50к×3600 + 100к×1200 + 200к×300 = 400 млн
CASSETTE_CAPS = {
    10_000:  4000,   # 40 млн
    50_000:  3600,   # 180 млн
    100_000: 1200,   # 120 млн
    200_000:  300,   # 60 млн
}
# Для обратной совместимости (один размер)
CASSETTE_CAPACITY = 3600                              # типовое значение (для 50к кассеты)

# Доля каждого номинала в обороте (эмпирика по УЗ рынку)
DENOMINATION_SHARE = {
    10_000:  0.10,   # 10%  = 40 млн
    50_000:  0.45,   # 45%  = 180 млн
    100_000: 0.30,   # 30%  = 120 млн
    200_000: 0.15,   # 15%  = 60 млн
}

# Стоимость одной полной кассеты по номиналу
CASSETTE_VALUE: Dict[int, int] = {
    denom: denom * CASSETTE_CAPS[denom]
    for denom in DENOMINATIONS
}

# Максимальный баланс банкомата при полной загрузке всех 4 кассет = 400 млн
MAX_BALANCE = sum(CASSETTE_VALUE.values())   # = 400_000_000


@dataclass
class Cassette:
    denomination: int                          # UZS номинал
    capacity:     int = field(default=0)       # купюр в кассете (зависит от номинала)
    count:        int = 0                      # текущее кол-во купюр

    def __post_init__(self):
        if self.capacity == 0:
            self.capacity = CASSETTE_CAPS.get(self.denomination, 500)

    @property
    def balance(self) -> int:
        return self.denomination * self.count

    @property
    def fill_pct(self) -> float:
        return self.count / self.capacity if self.capacity else 0

    @property
    def bills_to_fill(self) -> int:
        return self.capacity - self.count

    @property
    def value_to_fill(self) -> int:
        return self.bills_to_fill * self.denomination

    def to_dict(self) -> dict:
        return {
            "denomination":    self.denomination,
            "count":           self.count,
            "capacity":        self.capacity,
            "balance":         self.balance,
            "fill_pct":        round(self.fill_pct * 100, 1),
            "bills_to_fill":   self.bills_to_fill,
            "value_to_fill":   self.value_to_fill,
        }


@dataclass
class CassetteSet:
    """4 кассеты одного банкомата."""
    cassettes: List[Cassette] = field(default_factory=list)

    def __post_init__(self):
        if not self.cassettes:
            self.cassettes = [Cassette(denomination=d, capacity=CASSETTE_CAPS[d]) for d in DENOMINATIONS]

    @classmethod
    def from_balance(cls, total_balance: int) -> "CassetteSet":
        """
        Инициализирует кассеты из общего остатка.
        Распределяет по доле каждого номинала, не превышая ёмкость кассеты.
        """
        cs = cls()
        remaining = total_balance
        for cass in cs.cassettes:
            share = DENOMINATION_SHARE[cass.denomination]
            alloc = int(total_balance * share)
            bills = min(alloc // cass.denomination, cass.capacity)
            bills = max(0, bills)
            cass.count = bills
            remaining -= cass.balance

        # Остаток добиваем в 50k кассету
        if remaining > 0:
            c50 = cs.cassette(50_000)
            extra = min(remaining // 50_000, c50.bills_to_fill)
            c50.count += extra
        return cs

    def cassette(self, denomination: int) -> Cassette:
        for c in self.cassettes:
            if c.denomination == denomination:
                return c
        raise KeyError(denomination)

    @property
    def total_balance(self) -> int:
        return sum(c.balance for c in self.cassettes)

    @property
    def total_fill_pct(self) -> float:
        mx = sum(c.capacity * c.denomination for c in self.cassettes)
        return self.total_balance / mx if mx else 0

    def refill_cost(self) -> Tuple[int, int, List[dict]]:
        """
        Возвращает (total_bills_needed, total_value_needed, per_cassette_detail).
        """
        details = []
        total_bills = 0
        total_value = 0
        for c in self.cassettes:
            details.append({
                "denomination": c.denomination,
                "current_count": c.count,
                "current_balance": c.balance,
                "bills_to_fill": c.bills_to_fill,
                "value_to_fill": c.value_to_fill,
            })
            total_bills += c.bills_to_fill
            total_value += c.value_to_fill
        return total_bills, total_value, details

    def to_dict(self) -> dict:
        total_bills, total_value, details = self.refill_cost()
        return {
            "cassettes":       [c.to_dict() for c in self.cassettes],
            "total_balance":   self.total_balance,
            "total_fill_pct":  round(self.total_fill_pct * 100, 1),
            "bills_to_fill":   total_bills,
            "value_to_fill":   total_value,
        }


# ── Baseline сравнение (метод банка) ─────────────────────────

def baseline_refill(last_week_outcome: int, current_balance: int, sample_steps: int = 84) -> dict:
    """
    Воспроизводит текущую логику банка:
    «посмотрели прошлые транзакции и планируем пополнение на основе
    исторического спроса».

    Возвращает сравнение: baseline vs ML-рекомендация.
    """
    # Для честного сравнения с ML-прогнозом +24ч приводим недельную историю
    # к суточному плану: среднее снятие за день по прошлой неделе.
    observed_days = max(sample_steps / 12, 1)  # 12 двухчасовых шагов = 24 часа
    baseline_amount = round(last_week_outcome / observed_days)
    baseline_overfill = max(0, current_balance + baseline_amount - MAX_BALANCE)
    baseline_projected_balance = max(0, current_balance - baseline_amount)
    baseline_risk = "cash-out" if baseline_projected_balance < MAX_BALANCE * 0.20 else "ok"

    return {
        "method":           "Метод банка по прошлым транзакциям",
        "baseline_amount":  baseline_amount,
        "last_week_outcome": last_week_outcome,
        "observed_days":    round(observed_days, 1),
        "baseline_overfill": baseline_overfill,
        "baseline_risk":    baseline_risk,
        "frozen_cash":      baseline_overfill,
        "comment": (
            f"За прошлую неделю сняли {last_week_outcome/1e6:.0f} млн. "
            f"План банка на 24ч: {baseline_amount/1e6:.0f} млн."
        ),
    }

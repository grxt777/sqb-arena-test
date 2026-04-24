"""
Pydantic-схемы для всех API-ответов.
Строгая типизация — гарантия контракта между backend и frontend.
"""

from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class ATMMeta(BaseModel):
    atm_id:    str
    name:      str
    bank:      str
    profile:   str
    address:   str
    lat:       float
    lon:        float
    capacity:  int


class ATMState(BaseModel):
    atm_id:          str
    name:            str
    bank:            str
    lat:             float
    lon:             float
    capacity:        int
    balance:         int
    balance_pct:     float = Field(description="Остаток в % от ёмкости")
    status:          str   = Field(description="ok | warning | critical")
    is_incassation:  bool
    last_incassation: Optional[str]
    timestamp:       str


class MLPrediction(BaseModel):
    atm_id:              str
    pred_balance_24h:    float
    pred_balance_pct:    float
    cashout_prob:        float  = Field(description="Вероятность cash-out в следующие 24ч [0..1]")
    cashout_risk:        bool
    risk_label:          str    = Field(description="LOW | MEDIUM | HIGH")


class ATMFull(BaseModel):
    state:      ATMState
    prediction: Optional[MLPrediction] = None


class RouteStop(BaseModel):
    order:       int
    atm_id:      str
    name:        str
    bank:        str
    lat:         float
    lon:         float
    balance:     int
    balance_pct: float
    status:      str
    cashout_prob: Optional[float] = None
    dist_from_prev_km: float = 0.0


class CashRoute(BaseModel):
    car_id:       int
    label:        str
    color:        str
    stops:        List[RouteStop]
    total_dist_km: float
    est_time_min:  int


class RouteResponse(BaseModel):
    cars:          List[CashRoute]
    total_stops:   int
    total_dist_km: float
    est_time_min:  int
    depot:         dict


class SimStatus(BaseModel):
    current_timestamp: str
    step_index:        int
    total_steps:       int
    speed_x:           float   # во сколько раз быстрее реального времени


class AlertItem(BaseModel):
    atm_id:       str
    name:         str
    balance_pct:  float
    cashout_prob: float
    risk_label:   str
    hours_to_empty: Optional[float] = None

"""
Алгоритм построения маршрутов инкассации.

Алгоритм:
  1. Фильтруем ATM по статусу (critical / warning / all)
  2. Сортируем по приоритету: balance_pct ASC (самые пустые — первые)
     с учётом ML-вероятности cash-out если доступна
  3. Распределяем по машинам round-robin
  4. Для каждой машины — Greedy Nearest Neighbor от депо
     с весовым коэффициентом срочности
  5. Считаем расстояние и время (Haversine × road_factor)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from .config import DEPOT, ROAD_FACTOR, AVG_SPEED_KMH

CAR_COLORS = ["#f59e0b", "#3b82f6", "#a855f7", "#10b981"]
CAR_LABELS = ["Машина A", "Машина B", "Машина C", "Машина D"]

PRIORITY_WEIGHTS = {
    "critical": 0.5,   # кажется ближе — попадает в маршрут первым
    "warning":  0.75,
    "ok":       1.0,
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) \
        * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _greedy_route(
    atms: List[dict],
    start_lat: float,
    start_lon: float,
) -> List[dict]:
    """
    Жадный алгоритм ближайшего соседа с приоритетом срочности.
    atm dict должен содержать: lat, lon, status, cashout_prob (опционально).
    """
    unvisited = list(atms)
    route: List[dict] = []
    cur_lat, cur_lon = start_lat, start_lon

    while unvisited:
        best: Optional[dict] = None
        best_score = float("inf")

        for atm in unvisited:
            dist = haversine_km(cur_lat, cur_lon, atm["lat"], atm["lon"])
            weight = PRIORITY_WEIGHTS.get(atm.get("status", "ok"), 1.0)
            # Если есть ML — усиливаем приоритет по вероятности
            ml_w = 1.0 - atm.get("cashout_prob", 0.0) * 0.3
            score = dist * weight * ml_w
            if score < best_score:
                best_score = score
                best = atm

        route.append(best)
        unvisited.remove(best)
        cur_lat, cur_lon = best["lat"], best["lon"]

    return route


def _route_distance(stops: List[dict], depot: dict) -> float:
    """Расстояние маршрута: депо → stops → депо (с road_factor)."""
    pts = [(depot["lat"], depot["lon"])] + [(s["lat"], s["lon"]) for s in stops] \
          + [(depot["lat"], depot["lon"])]
    total = sum(haversine_km(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])
                for i in range(len(pts) - 1))
    return round(total * ROAD_FACTOR, 2)


def build_routes(
    atm_states: Dict[str, dict],
    atm_predictions: Dict[str, dict],
    filter_status: str = "warning",   # "critical" | "warning" | "all"
    num_cars: int = 2,
    speed_kmh: float = AVG_SPEED_KMH,
) -> dict:
    """
    Строит оптимальные маршруты инкассации.

    Returns:
        {
          cars: [{ car_id, label, color, stops, total_dist_km, est_time_min }],
          total_stops, total_dist_km, est_time_min, depot
        }
    """
    # 1. Фильтрация
    candidates = []
    for atm_id, st in atm_states.items():
        status = st.get("status", "ok")
        if filter_status == "critical" and status != "critical":
            continue
        if filter_status == "warning" and status not in ("critical", "warning"):
            continue

        ml = atm_predictions.get(atm_id, {})
        candidates.append({
            "atm_id":       atm_id,
            "name":         st["name"],
            "bank":         st.get("bank", ""),
            "lat":          st["lat"],
            "lon":          st["lon"],
            "balance":      st["balance"],
            "balance_pct":  st["balance_pct"],
            "status":       status,
            "cashout_prob": ml.get("cashout_prob", 0.0),
            "capacity":     st.get("capacity", 0),
        })

    if not candidates:
        return {"cars": [], "total_stops": 0, "total_dist_km": 0.0,
                "est_time_min": 0, "depot": DEPOT}

    # 2. Сортировка по приоритету (balance_pct ASC, затем cashout_prob DESC)
    candidates.sort(key=lambda x: (x["balance_pct"] - x["cashout_prob"] * 20))

    # 3. Round-robin распределение по машинам
    groups: List[List[dict]] = [[] for _ in range(min(num_cars, len(candidates)))]
    for i, atm in enumerate(candidates):
        groups[i % len(groups)].append(atm)

    # 4. Построение маршрута для каждой машины
    cars = []
    total_dist  = 0.0
    total_stops = 0

    for gi, group in enumerate(groups):
        if not group:
            continue

        ordered = _greedy_route(group, DEPOT["lat"], DEPOT["lon"])
        dist_km = _route_distance(ordered, DEPOT)
        time_min = round(dist_km / speed_kmh * 60)

        stops = []
        prev_lat, prev_lon = DEPOT["lat"], DEPOT["lon"]
        for si, atm in enumerate(ordered):
            d = round(haversine_km(prev_lat, prev_lon, atm["lat"], atm["lon"]) * ROAD_FACTOR, 2)
            stops.append({
                "order":            si + 1,
                "atm_id":           atm["atm_id"],
                "name":             atm["name"],
                "bank":             atm["bank"],
                "lat":              atm["lat"],
                "lon":              atm["lon"],
                "balance":          atm["balance"],
                "balance_pct":      atm["balance_pct"],
                "status":           atm["status"],
                "cashout_prob":     atm["cashout_prob"],
                "dist_from_prev_km": d,
            })
            prev_lat, prev_lon = atm["lat"], atm["lon"]

        total_dist  += dist_km
        total_stops += len(stops)
        cars.append({
            "car_id":        gi,
            "label":         CAR_LABELS[gi % len(CAR_LABELS)],
            "color":         CAR_COLORS[gi % len(CAR_COLORS)],
            "stops":         stops,
            "total_dist_km": dist_km,
            "est_time_min":  time_min,
        })

    return {
        "cars":          cars,
        "total_stops":   total_stops,
        "total_dist_km": round(total_dist, 2),
        "est_time_min":  round(total_dist / speed_kmh * 60),
        "depot":         DEPOT,
    }

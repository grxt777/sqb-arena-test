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

import httpx

from .config import (
    DEPOT,
    ROAD_FACTOR,
    AVG_SPEED_KMH,
    CASHOUT_THRESHOLD,
    ROUTING_PROVIDER,
    OSRM_BASE_URL,
    ROUTING_TIMEOUT_SECS,
)

CAR_COLORS = ["#f59e0b", "#3b82f6", "#a855f7", "#10b981"]
CAR_LABELS = ["Машина A", "Машина B", "Машина C", "Машина D"]

PRIORITY_WEIGHTS = {
    "critical": 0.5,   # кажется ближе — попадает в маршрут первым
    "warning":  0.75,
    "ok":       1.0,
}

STATUS_SCORE = {
    "critical": 40.0,
    "warning": 18.0,
    "ok": 0.0,
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
    return round(_path_distance(stops, depot), 2)


def _path_distance(stops: List[dict], depot: dict) -> float:
    """Расстояние маршрута без округления: депо → stops → депо."""
    pts = [(depot["lat"], depot["lon"])] + [(s["lat"], s["lon"]) for s in stops] \
          + [(depot["lat"], depot["lon"])]
    total = sum(haversine_km(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])
                for i in range(len(pts) - 1))
    return total * ROAD_FACTOR


def _two_opt_route(stops: List[dict], depot: dict, max_iter: int = 80) -> List[dict]:
    """Локальная оптимизация порядка остановок, чтобы убрать лишние пересечения."""
    if len(stops) < 4:
        return stops

    best = list(stops)
    best_dist = _path_distance(best, depot)
    improved = True
    iteration = 0

    while improved and iteration < max_iter:
        improved = False
        iteration += 1
        for i in range(len(best) - 2):
            for j in range(i + 2, len(best)):
                if i == 0 and j == len(best) - 1:
                    continue
                candidate = best[:i + 1] + list(reversed(best[i + 1:j + 1])) + best[j + 1:]
                cand_dist = _path_distance(candidate, depot)
                if cand_dist + 0.01 < best_dist:
                    best = candidate
                    best_dist = cand_dist
                    improved = True
                    break
            if improved:
                break

    return best


def _priority_score(st: dict, ml: dict) -> float:
    """Бизнес-приоритет: остаток, ML риск, статус, кассеты и время до empty."""
    balance_pct = float(st.get("balance_pct", 100.0))
    cashout_prob = float(ml.get("cashout_prob", 0.0) or 0.0)
    status = st.get("status", "ok")
    cassette_need = float(st.get("cassettes_value_to_fill", 0) or 0)
    capacity = float(st.get("capacity", 1) or 1)
    cassette_need_pct = min(cassette_need / capacity * 100.0, 100.0)

    empty_bonus = 0.0
    hours_to_empty = st.get("hours_to_empty")
    if hours_to_empty is not None:
        if hours_to_empty <= 6:
            empty_bonus = 20.0
        elif hours_to_empty <= 12:
            empty_bonus = 10.0
        elif hours_to_empty <= 24:
            empty_bonus = 5.0

    return round(
        STATUS_SCORE.get(status, 0.0)
        + (100.0 - balance_pct) * 0.45
        + cashout_prob * 35.0
        + cassette_need_pct * 0.15
        + empty_bonus,
        2,
    )


def _route_center(group: List[dict]) -> tuple[float, float]:
    """Приоритетно-взвешенный центр группы ATM."""
    if not group:
        return DEPOT["lat"], DEPOT["lon"]
    total_w = sum(max(a.get("priority_score", 1.0), 1.0) for a in group)
    lat = sum(a["lat"] * max(a.get("priority_score", 1.0), 1.0) for a in group) / total_w
    lon = sum(a["lon"] * max(a.get("priority_score", 1.0), 1.0) for a in group) / total_w
    return lat, lon


def _assign_to_cars(candidates: List[dict], num_cars: int) -> List[List[dict]]:
    """Назначает ATM машинам по срочности, географии и балансу нагрузки."""
    cars_count = min(max(num_cars, 1), len(candidates))
    groups: List[List[dict]] = [[] for _ in range(cars_count)]
    ordered = sorted(candidates, key=lambda x: x["priority_score"], reverse=True)

    for i in range(cars_count):
        groups[i].append(ordered[i])

    avg_group_size = max(len(candidates) / cars_count, 1)
    for atm in ordered[cars_count:]:
        best_idx = 0
        best_score = float("inf")
        for gi, group in enumerate(groups):
            c_lat, c_lon = _route_center(group)
            dist_to_cluster = haversine_km(c_lat, c_lon, atm["lat"], atm["lon"]) * ROAD_FACTOR
            load_penalty = (len(group) / avg_group_size) * 1.8
            urgency_discount = atm["priority_score"] / 120.0
            score = dist_to_cluster + load_penalty - urgency_discount
            if score < best_score:
                best_score = score
                best_idx = gi
        groups[best_idx].append(atm)

    return groups


def _baseline_round_robin_distance(candidates: List[dict], num_cars: int) -> float:
    """Старый подход проекта: round-robin + greedy nearest-neighbor."""
    if not candidates:
        return 0.0
    groups: List[List[dict]] = [[] for _ in range(min(max(num_cars, 1), len(candidates)))]
    ordered = sorted(candidates, key=lambda x: (x["balance_pct"] - x["cashout_prob"] * 20))
    for i, atm in enumerate(ordered):
        groups[i % len(groups)].append(atm)

    total = 0.0
    for group in groups:
        if not group:
            continue
        total += _route_distance(_greedy_route(group, DEPOT["lat"], DEPOT["lon"]), DEPOT)
    return round(total, 2)


def _fallback_geometry(stops: List[dict], depot: dict) -> List[dict]:
    """Прямая линия как fallback, если OSRM недоступен."""
    pts = [depot] + stops + [depot]
    return [{"lat": p["lat"], "lon": p["lon"]} for p in pts]


def _road_route_geometry(stops: List[dict], depot: dict) -> dict:
    """
    Строит реальную дорожную геометрию через OSRM.
    Возвращает fallback-линию, если внешний routing engine недоступен.
    """
    fallback = {
        "ok": False,
        "provider": "fallback",
        "geometry": _fallback_geometry(stops, depot),
        "distance_km": _route_distance(stops, depot),
        "duration_min": None,
        "error": None,
    }

    if ROUTING_PROVIDER != "osrm" or not stops:
        return fallback

    waypoints = [depot] + stops + [depot]
    coords = ";".join(f"{p['lon']:.6f},{p['lat']:.6f}" for p in waypoints)
    url = f"{OSRM_BASE_URL.rstrip('/')}/route/v1/driving/{coords}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
        "annotations": "false",
    }

    try:
        with httpx.Client(timeout=ROUTING_TIMEOUT_SECS) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        routes = data.get("routes") or []
        if not routes:
            fallback["error"] = "OSRM returned no routes"
            return fallback

        route = routes[0]
        raw_coords = route.get("geometry", {}).get("coordinates") or []
        geometry = [{"lat": lat, "lon": lon} for lon, lat in raw_coords]

        if len(geometry) < 2:
            fallback["error"] = "OSRM returned empty geometry"
            return fallback

        return {
            "ok": True,
            "provider": "osrm",
            "geometry": geometry,
            "distance_km": round(float(route.get("distance", 0.0)) / 1000.0, 2),
            "duration_min": round(float(route.get("duration", 0.0)) / 60.0),
            "error": None,
        }
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback


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
        ml = atm_predictions.get(atm_id, {})
        risk_label = ml.get("risk_label", "LOW")
        cashout_prob = float(ml.get("cashout_prob", 0.0) or 0.0)
        is_ml_high = risk_label == "HIGH" or cashout_prob >= 0.65
        is_ml_risky = risk_label in ("HIGH", "MEDIUM") or cashout_prob >= CASHOUT_THRESHOLD

        if filter_status == "critical" and status != "critical" and not is_ml_high:
            continue
        if filter_status == "warning" and status not in ("critical", "warning") and not is_ml_risky:
            continue

        priority = _priority_score(st, ml)
        refill_amount = max(0, st.get("capacity", 0) - st.get("balance", 0))
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
            "risk_label":   ml.get("risk_label", "LOW"),
            "capacity":     st.get("capacity", 0),
            "priority_score": priority,
            "refill_amount": refill_amount,
            "empty_at": st.get("empty_at"),
            "cassettes_value_to_fill": st.get("cassettes_value_to_fill", 0),
        })

    if not candidates:
        return {"cars": [], "total_stops": 0, "total_dist_km": 0.0,
                "est_time_min": 0, "depot": DEPOT}

    # 2. Сортировка по бизнес-приоритету
    candidates.sort(key=lambda x: x["priority_score"], reverse=True)

    # Старый baseline проекта для демонстрации эффективности
    baseline_old_km = _baseline_round_robin_distance(candidates, num_cars)

    # 3. Географическое распределение по машинам
    groups = _assign_to_cars(candidates, num_cars)

    # 4. Построение маршрута для каждой машины
    cars = []
    total_dist  = 0.0
    total_stops = 0
    fleet_time_min = 0

    for gi, group in enumerate(groups):
        if not group:
            continue

        ordered = _greedy_route(group, DEPOT["lat"], DEPOT["lon"])
        ordered = _two_opt_route(ordered, DEPOT)
        road = _road_route_geometry(ordered, DEPOT)
        dist_km = road["distance_km"]
        time_min = road["duration_min"] if road["duration_min"] is not None \
            else round(dist_km / speed_kmh * 60)
        refill_total = sum(a.get("refill_amount", 0) for a in ordered)

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
                "risk_label":       atm["risk_label"],
                "priority_score":   atm["priority_score"],
                "refill_amount":    atm["refill_amount"],
                "empty_at":         atm.get("empty_at"),
                "cassettes_value_to_fill": atm.get("cassettes_value_to_fill", 0),
                "dist_from_prev_km": d,
            })
            prev_lat, prev_lon = atm["lat"], atm["lon"]

        total_dist  += dist_km
        total_stops += len(stops)
        fleet_time_min = max(fleet_time_min, time_min)
        cars.append({
            "car_id":        gi,
            "label":         CAR_LABELS[gi % len(CAR_LABELS)],
            "color":         CAR_COLORS[gi % len(CAR_COLORS)],
            "stops":         stops,
            "total_dist_km": dist_km,
            "est_time_min":  time_min,
            "refill_total":  refill_total,
            "geometry":      road["geometry"],
            "routing_provider": road["provider"],
            "routing_error": road["error"],
        })

    saved_km = max(0.0, baseline_old_km - total_dist)

    return {
        "cars":          cars,
        "total_stops":   total_stops,
        "total_dist_km": round(total_dist, 2),
        "est_time_min":  fleet_time_min,
        "depot":         DEPOT,
        "algorithm":     "priority_cluster_2opt",
        "route_quality": {
            "baseline_old_km": baseline_old_km,
            "optimized_km": round(total_dist, 2),
            "saved_km": round(saved_km, 2),
            "saved_pct": round(saved_km / max(baseline_old_km, 0.01) * 100, 1),
        },
    }

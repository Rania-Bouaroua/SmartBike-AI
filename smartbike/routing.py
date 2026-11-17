"""Rebalancing truck tours: which stations to visit, in which order, and how many bikes to load / unload.

Problem (one-commodity pickup-and-delivery VRP with prizes): K trucks of capacity Q start empty, pick bikes up at
over-full stations and drop them at stations about to run empty. Each candidate station i has a signed need
`delta_i` (+ = deliver, - = pick up) and a value (its priority score). Within a shift of T minutes, choose and order
the stops to maximise the value served while minimising driving; a stop can be skipped at the price of its value.

Solved with Google OR-Tools (routing solver, guided local search, time-boxed). The truck load is a capacity dimension
whose cumulative value must stay in [0, Q]; time is a second dimension (driving + service time). Routes are open:
trucks start where they are (a zero-cost virtual depot), which matches mid-shift re-planning.

`greedy_plan` is the honest baseline: follow the priority list (what the dashboard showed before), skipping a stop
only when the load makes it impossible, until the shift ends.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

EARTH_KM = 6371.0


@dataclass(frozen=True)
class FleetConfig:
    n_trucks: int = 3
    truck_capacity: int = 30          # bikes per truck (typical Citi Bike box truck / trailer)
    shift_minutes: int = 240
    speed_kmh: float = 15.0           # average urban truck speed incl. traffic
    detour_factor: float = 1.3        # street network vs great-circle distance (Manhattan grid ~1.27)
    service_min: float = 4.0          # park + unlock per stop
    per_bike_min: float = 0.25        # handling time per bike moved
    time_limit_s: int = 15            # solver time box
    seed: int = 0


def distance_matrix_km(lat: np.ndarray, lon: np.ndarray, detour: float = 1.3) -> np.ndarray:
    lat, lon = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    dlat = lat[:, None] - lat[None, :]
    dlon = lon[:, None] - lon[None, :]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat)[:, None] * np.cos(lat)[None, :] * np.sin(dlon / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1))) * detour


def _prepare(stops: pd.DataFrame, cfg: FleetConfig) -> pd.DataFrame:
    need = {"station_id", "lat", "lon", "delta", "value"}
    missing = need - set(stops.columns)
    if missing:
        raise ValueError(f"stops is missing columns {sorted(missing)}")
    s = stops[stops.delta != 0].dropna(subset=["lat", "lon"]).copy().reset_index(drop=True)
    # one visit per station: a need larger than a truck is served up to the truck capacity
    s["delta"] = s.delta.clip(-cfg.truck_capacity, cfg.truck_capacity).astype(int)
    return s


def _summarise(s: pd.DataFrame, routes: list[list[int]], dist: np.ndarray, cfg: FleetConfig,
               method: str) -> tuple[pd.DataFrame, dict]:
    rows, km_total = [], 0.0
    for truck, route in enumerate(routes):
        load, minutes, prev = 0, 0.0, None
        for order, i in enumerate(route):
            leg = 0.0 if prev is None else dist[prev, i]
            km_total += leg
            minutes += leg / cfg.speed_kmh * 60 + cfg.service_min + cfg.per_bike_min * abs(s.delta[i])
            load -= s.delta[i]
            rows.append({"method": method, "truck": truck + 1, "stop": order + 1, **s.loc[i].to_dict(),
                         "leg_km": round(leg, 2), "load_after": int(load), "minutes_cum": round(minutes, 1)})
            prev = i
    plan = pd.DataFrame(rows)
    served = set(plan.station_id) if not plan.empty else set()
    kpi = {"method": method, "stops": len(plan), "bikes_moved": int(plan.delta.abs().sum()) if len(plan) else 0,
           "value_served": float(plan.value.sum()) if len(plan) else 0.0,
           "value_share": float(plan.value.sum() / s.value.sum()) if len(plan) and s.value.sum() else 0.0,
           "km": round(float(km_total), 1), "candidates": len(s),
           "unserved": int(len(s) - len(served))}
    kpi["bikes_per_km"] = round(float(kpi["bikes_moved"] / kpi["km"]), 2) if kpi["km"] else 0.0
    return plan, kpi


def _feasible_time(minutes: float, leg_km: float, delta: int, cfg: FleetConfig) -> float:
    return minutes + leg_km / cfg.speed_kmh * 60 + cfg.service_min + cfg.per_bike_min * abs(delta)


def greedy_plan(stops: pd.DataFrame, cfg: FleetConfig = FleetConfig()) -> tuple[pd.DataFrame, dict]:
    """Baseline: trucks take the priority list in order (round-robin), skipping stops their load cannot serve."""
    s = _prepare(stops, cfg).sort_values("value", ascending=False).reset_index(drop=True)
    dist = distance_matrix_km(s.lat, s.lon, cfg.detour_factor)
    routes: list[list[int]] = [[] for _ in range(cfg.n_trucks)]
    state = [{"load": 0, "min": 0.0, "pos": None} for _ in range(cfg.n_trucks)]
    for i in range(len(s)):
        d = int(s.delta[i])
        for t in sorted(range(cfg.n_trucks), key=lambda k: state[k]["min"]):   # least busy truck first
            st = state[t]
            new_load = st["load"] - d
            leg = 0.0 if st["pos"] is None else dist[st["pos"], i]
            end = _feasible_time(st["min"], leg, d, cfg)
            if 0 <= new_load <= cfg.truck_capacity and end <= cfg.shift_minutes:
                routes[t].append(i)
                st.update(load=new_load, min=end, pos=i)
                break
    return _summarise(s, routes, dist, cfg, "greedy (liste de priorité)")


def optimise_plan(stops: pd.DataFrame, cfg: FleetConfig = FleetConfig()) -> tuple[pd.DataFrame, dict]:
    """OR-Tools plan maximising value served minus driving cost, under load and shift-time constraints."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    s = _prepare(stops, cfg)
    n = len(s)
    if n == 0:
        return _summarise(s, [], np.zeros((0, 0)), cfg, "optimisé (OR-Tools)")
    dist = distance_matrix_km(s.lat, s.lon, cfg.detour_factor)
    # node 0 = virtual depot (open routes: zero cost to / from it)
    dm = np.zeros((n + 1, n + 1))
    dm[1:, 1:] = dist
    metres = np.round(dm * 1000).astype(int)
    service = np.r_[0.0, cfg.service_min + cfg.per_bike_min * np.abs(s.delta.to_numpy())]
    travel_min = dm / cfg.speed_kmh * 60
    load_change = np.r_[0, -s.delta.to_numpy()].astype(int)          # pick-up (+) increases the truck load

    manager = pywrapcp.RoutingIndexManager(n + 1, cfg.n_trucks, 0)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(i, j):
        return int(metres[manager.IndexToNode(i), manager.IndexToNode(j)])

    def time_cb(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return int(round(service[a] + travel_min[a, b]))

    def load_cb(i):
        return int(load_change[manager.IndexToNode(i)])

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(dist_cb))
    routing.AddDimension(routing.RegisterTransitCallback(time_cb), 0, cfg.shift_minutes, True, "time")
    routing.AddDimensionWithVehicleCapacity(routing.RegisterUnaryTransitCallback(load_cb), 0,
                                            [cfg.truck_capacity] * cfg.n_trucks, True, "load")
    # skipping a station costs its value, expressed in metres of driving: 1 value point = 100 m
    for i in range(n):
        routing.AddDisjunction([manager.NodeToIndex(i + 1)], int(round(100 * s.value[i])) + 1)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = cfg.time_limit_s
    solution = routing.SolveWithParameters(params)
    if solution is None:
        raise RuntimeError("OR-Tools found no feasible plan")
    routes = []
    for v in range(cfg.n_trucks):
        idx, route = routing.Start(v), []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node:
                route.append(node - 1)
            idx = solution.Value(routing.NextVar(idx))
        routes.append(route)
    return _summarise(s, routes, dist, cfg, "optimisé (OR-Tools)")


def candidates_from_priority(priority: pd.DataFrame, top_n: int = 80) -> pd.DataFrame:
    """Turn the station priority table (risk.assess output) into routing candidates."""
    p = priority[priority.bikes_to_move != 0].sort_values("priority_rank").head(top_n)
    return pd.DataFrame({"station_id": p.station_id, "station_name": p.station_name, "lat": p.lat, "lon": p.lon,
                         "delta": p.bikes_to_move.astype(int), "value": p.priority_score.astype(float),
                         "risk": p.risk})

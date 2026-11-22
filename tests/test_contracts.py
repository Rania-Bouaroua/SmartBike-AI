"""Data contracts must accept the real shape of each table and reject the failures consumers cannot survive."""
import pandas as pd
import pytest

from smartbike.contracts import CONTRACTS, DataContractError, validate


def priority(**over):
    df = pd.DataFrame({
        "station_id": ["a", "b", "c"], "capacity": [20.0, 20.0, 20.0], "num_bikes_available": [0, 18, 10],
        "num_docks_available": [20, 2, 10], "pred_departures": [1.0, 0.5, 1.0], "pred_arrivals": [1.0, 3.0, 1.0],
        "p_empty": [1.0, 0.0, 0.05], "p_full": [0.0, 0.7, 0.01],
        "risk": ["vide maintenant", "risque saturee", "ok"], "bikes_to_move": [10, -5, 0],
        "action": ["ajouter des velos", "retirer des velos", "aucune"], "priority_rank": [1, 2, 3],
        "lat": [40.7, 40.75, 40.8], "lon": [-73.99, -73.98, -73.95]})
    for k, v in over.items():
        df[k] = v
    return df


def test_valid_priority_table_passes():
    validate("gold_station_priority", priority())
    validate("unknown_table_without_contract", pd.DataFrame({"x": [1]}))       # no contract -> no-op


@pytest.mark.parametrize("change, message", [
    ({"p_empty": [1.4, 0.0, 0.1]}, "p_empty"),                                       # not a probability
    ({"station_id": ["a", "a", "c"]}, "station_id"),                                 # duplicate key
    ({"risk": ["vide maintenant", "bientot vide", "ok"]}, "risk"),                   # unknown label
    ({"action": ["retirer des velos", "retirer des velos", "aucune"]}, "sign of bikes_to_move"),
    ({"bikes_to_move": [10, -5, 3], "action": ["ajouter des velos", "retirer des velos", "ajouter des velos"]},
     "if and only if"),                                                              # 'ok' station with a move
    ({"lat": [40.7, 48.85, 40.8]}, "lat"),                                           # Paris is not in New York
])
def test_contract_rejects_what_consumers_cannot_survive(change, message):
    with pytest.raises(DataContractError, match=message):
        validate("gold_station_priority", priority(**change))


def test_truck_routes_contract():
    ok = pd.DataFrame({"method": ["opt"] * 2, "truck": [1, 1], "stop": [1, 2], "station_id": ["a", "b"],
                       "delta": [-10, 8], "load_after": [10, 2], "minutes_cum": [6.5, 14.0], "leg_km": [0.0, 1.2]})
    validate("gold_truck_routes", ok)
    with pytest.raises(DataContractError, match="load_after"):
        validate("gold_truck_routes", ok.assign(load_after=[10, -2]))
    with pytest.raises(DataContractError, match="visited once"):
        validate("gold_truck_routes", ok.assign(station_id=["a", "a"]))


def test_every_contract_is_a_schema():
    assert {"silver_stations", "gold_station_priority", "gold_truck_routes"} <= set(CONTRACTS)


def test_model_comparison_contract():
    ok = pd.DataFrame({"target": ["departures"] * 3, "model": ["baseline", "ridge", "hgb_poisson"],
                       "val_poisson_dev": [1.3, 1.5, 1.2], "val_mae": [1.2, 1.2, 1.19],
                       "selected": [False, False, True]})
    validate("gold_model_comparison", ok)
    with pytest.raises(DataContractError, match="exactly one selected"):
        validate("gold_model_comparison", ok.assign(selected=[False, True, True]))
    with pytest.raises(DataContractError, match="baseline is not a model"):
        validate("gold_model_comparison", ok.assign(selected=[True, False, False]))

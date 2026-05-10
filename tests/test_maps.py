from src.maps import ZURICH_HB, ZURICH_HB_NAME, _summarize_route, _waypoint


def _walk(seconds: int) -> dict:
    return {"travelMode": "WALK", "staticDuration": f"{seconds}s"}


def _rail(line: str, seconds: int, arrival_stop: str) -> dict:
    return {
        "travelMode": "TRANSIT",
        "staticDuration": f"{seconds}s",
        "transitDetails": {
            "transitLine": {
                "nameShort": line,
                "vehicle": {"type": "HEAVY_RAIL"},
            },
            "stopDetails": {"arrivalStop": {"name": arrival_stop}},
        },
    }


def _route(steps: list[dict]) -> dict:
    return {"legs": [{"steps": steps}]}


def test_summarize_route_drops_final_hb_walk():
    summary = _summarize_route(_route([
        _walk(360),
        _rail("IR35", 840, "Zürich HB"),
        _walk(90),
        _walk(30),
    ]))

    assert summary["modes"] == ["WALK", "HEAVY_RAIL IR35"]
    assert summary["travel_min"] == 20
    assert summary["dropped_final_hb_walk_min"] == 2
    assert summary["arrives_at"] == "Zürich HB"


def test_summarize_route_keeps_non_hb_final_walk():
    summary = _summarize_route(_route([
        _walk(360),
        _rail("IR35", 840, "Zürich Enge"),
        _walk(120),
    ]))

    assert summary["modes"] == ["WALK", "HEAVY_RAIL IR35", "WALK"]
    assert summary["travel_min"] == 22
    assert "dropped_final_hb_walk_min" not in summary


def test_waypoint_supports_name_and_legacy_coordinates():
    assert _waypoint(ZURICH_HB_NAME) == {"address": "Zürich HB, Zürich, Switzerland"}
    assert _waypoint(ZURICH_HB) == {
        "location": {
            "latLng": {
                "latitude": 47.378177,
                "longitude": 8.540192,
            },
        },
    }

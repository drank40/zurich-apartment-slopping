"""Google Routes API commute helper. Models a Monday 09:00 local commute
(deterministic; current time ignored). If walking is shorter than
WALK_THRESHOLD_MIN, transit is skipped. Returns top 3 transit alternatives
sorted by active commute time (excludes initial wait at first stop)."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
import unicodedata
import requests

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ZURICH_HB = (47.378177, 8.540192)
ZURICH_HB_NAME = "Zürich HB, Zürich, Switzerland"
WALK_THRESHOLD_MIN = 18
LOCAL_TZ = timezone(timedelta(hours=2))
TOP_N = 3


def _next_monday_9am() -> datetime:
    now = datetime.now(LOCAL_TZ)
    days = (7 - now.weekday()) % 7 or 7
    return (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)


def _dur_s(d: str) -> int:
    return int(d[:-1])  # "123s" -> 123


def _norm_stop_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_name.lower().replace(",", " ").replace("/", " ").split())


def _is_zurich_hb_stop(name: str | None) -> bool:
    if not name:
        return False
    norm = _norm_stop_name(name)
    return "zurich" in norm and (" hb" in f" {norm} " or "hauptbahnhof" in norm)


def _waypoint(value: tuple[float, float] | str) -> dict[str, Any]:
    if isinstance(value, str):
        return {"address": value}
    return {"location": {"latLng": {"latitude": value[0], "longitude": value[1]}}}


def _summarize_route(route: dict) -> dict[str, Any]:
    """Routes API with departureTime already excludes initial wait at first
    stop (it shifts the route start so user leaves origin just-in-time).
    Transfer waits are included in route.duration."""
    steps = route["legs"][0].get("steps", [])
    modes: list[str] = []
    display_steps: list[dict[str, Any]] = []
    last_transit_arrival: str | None = None
    for step in steps:
        if step.get("travelMode") == "TRANSIT":
            tl = step.get("transitDetails", {}).get("transitLine", {})
            mode = f"{tl.get('vehicle', {}).get('type', '?')}{(' ' + tl.get('nameShort', '')).rstrip()}"
            stop = step.get("transitDetails", {}).get("stopDetails", {}).get("arrivalStop", {})
            last_transit_arrival = stop.get("name")
            display_steps.append({"mode": mode, "duration_s": _dur_s(step.get("staticDuration", "0s"))})
        else:
            display_steps.append({"mode": "WALK", "duration_s": _dur_s(step.get("staticDuration", "0s"))})

    dropped_final_hb_walk_s = 0
    if display_steps and display_steps[-1]["mode"] == "WALK" and _is_zurich_hb_stop(last_transit_arrival):
        # Google often models an in-station/platform-to-coordinate walk after
        # arriving at Zurich HB. For an HB-target commute, reaching the HB stop
        # is the meaningful arrival point, so keep that walk out of the score.
        while display_steps and display_steps[-1]["mode"] == "WALK":
            dropped_final_hb_walk_s += display_steps.pop()["duration_s"]

    for step in display_steps:
        mode = step["mode"]
        if mode != "WALK" or not modes or modes[-1] != "WALK":
            modes.append(mode)

    travel_s = sum(s["duration_s"] for s in display_steps)
    out = {"modes": modes, "travel_min": round(travel_s / 60)}
    if dropped_final_hb_walk_s:
        out["dropped_final_hb_walk_min"] = round(dropped_final_hb_walk_s / 60)
        out["arrives_at"] = last_transit_arrival
    return out


class Commuter:
    def __init__(self, api_key: str, dest: tuple[float, float] | str = ZURICH_HB_NAME):
        self.api_key = api_key
        self.dest = dest

    def _post(self, origin: tuple[float, float], travel_mode: str,
              depart_iso: str | None, fields: Iterable[str], alternatives: bool = False) -> dict:
        body: dict[str, Any] = {
            "origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
            "destination": _waypoint(self.dest),
            "travelMode": travel_mode,
            "computeAlternativeRoutes": alternatives,
        }
        if travel_mode == "TRANSIT":
            body["departureTime"] = depart_iso
            body["transitPreferences"] = {"routingPreference": "FEWER_TRANSFERS"}
        r = requests.post(ROUTES_URL, json=body, headers={
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": ",".join(fields),
            "Content-Type": "application/json",
        }, timeout=15)
        r.raise_for_status()
        return r.json()

    def commute(self, origin: tuple[float, float]) -> dict[str, Any]:
        depart = _next_monday_9am()
        depart_utc_iso = depart.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        walk = self._post(origin, "WALK", None, ["routes.duration", "routes.distanceMeters"])
        walk_min = round(_dur_s(walk["routes"][0]["duration"]) / 60) if walk.get("routes") else None
        walk_km = walk["routes"][0]["distanceMeters"] / 1000 if walk.get("routes") else None

        if walk_min is not None and walk_min <= WALK_THRESHOLD_MIN:
            return {"depart": depart_utc_iso, "walk_min": walk_min, "walk_km": walk_km,
                    "alternatives": [{"modes": ["WALK"], "travel_min": walk_min}]}

        transit = self._post(origin, "TRANSIT", depart_utc_iso, [
            "routes.duration",
            "routes.legs.steps.travelMode",
            "routes.legs.steps.staticDuration",
            "routes.legs.steps.transitDetails.transitLine.vehicle.type",
            "routes.legs.steps.transitDetails.transitLine.nameShort",
            "routes.legs.steps.transitDetails.stopDetails.arrivalStop.name",
            "routes.legs.steps.transitDetails.stopDetails.departureTime",
        ], alternatives=True)
        alts = [_summarize_route(r) for r in transit.get("routes", [])]
        alts.sort(key=lambda a: a["travel_min"])
        return {"depart": depart_utc_iso, "walk_min": walk_min, "walk_km": walk_km,
                "alternatives": alts[:TOP_N]}


if __name__ == "__main__":
    import os, sys, json
    key = os.environ.get("GOOGLE_MAPS_KEY") or (sys.argv[1] if len(sys.argv) > 1 else "")
    origins = {
        "Thalwil (Aegertlistrasse 18)": (47.295, 8.564),
        "Wallisellen (Bahnhofstr. 30)": (47.412, 8.601),
        "Zurich 8038 (Geerenweg)": (47.353, 8.515),
    }
    c = Commuter(key)
    for name, o in origins.items():
        print(f"\n=== {name} -> Zurich HB ===")
        print(json.dumps(c.commute(o), indent=2))

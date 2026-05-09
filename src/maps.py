"""Google Routes API commute helper. Models a Monday 09:00 local commute
(deterministic; current time ignored). If walking is shorter than
WALK_THRESHOLD_MIN, transit is skipped. Returns top 3 transit alternatives
sorted by active commute time (excludes initial wait at first stop)."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
import requests

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ZURICH_HB = (47.378177, 8.540192)
WALK_THRESHOLD_MIN = 18
LOCAL_TZ = timezone(timedelta(hours=2))
TOP_N = 3


def _next_monday_9am() -> datetime:
    now = datetime.now(LOCAL_TZ)
    days = (7 - now.weekday()) % 7 or 7
    return (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)


def _dur_s(d: str) -> int:
    return int(d[:-1])  # "123s" -> 123


def _summarize_route(route: dict) -> dict[str, Any]:
    """Routes API with departureTime already excludes initial wait at first
    stop (it shifts the route start so user leaves origin just-in-time).
    Transfer waits are included in route.duration."""
    steps = route["legs"][0].get("steps", [])
    modes: list[str] = []
    for step in steps:
        if step.get("travelMode") == "TRANSIT":
            tl = step.get("transitDetails", {}).get("transitLine", {})
            modes.append(f"{tl.get('vehicle', {}).get('type', '?')}{(' ' + tl.get('nameShort', '')).rstrip()}")
        elif not modes or modes[-1] != "WALK":
            modes.append("WALK")
    travel_s = sum(_dur_s(s.get("staticDuration", "0s")) for s in steps)
    return {"modes": modes, "travel_min": round(travel_s / 60)}


class Commuter:
    def __init__(self, api_key: str, dest: tuple[float, float] = ZURICH_HB):
        self.api_key = api_key
        self.dest = dest

    def _post(self, origin: tuple[float, float], travel_mode: str,
              depart_iso: str | None, fields: Iterable[str], alternatives: bool = False) -> dict:
        body: dict[str, Any] = {
            "origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
            "destination": {"location": {"latLng": {"latitude": self.dest[0], "longitude": self.dest[1]}}},
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

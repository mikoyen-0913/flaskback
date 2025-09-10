# utils_geo.py
import os
import requests
import certifi
from urllib.parse import urlencode
from typing import Optional, Tuple, List

TIMEOUT = 6
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

def _get(url, headers=None, timeout=TIMEOUT):
    try:
        return requests.get(url, headers=headers or {}, timeout=timeout, verify=certifi.where())
    except Exception:
        return None

def geocode_google(addr: str) -> Optional[Tuple[float, float]]:
    """用 Google Maps API 轉換地址 → (lat, lng)"""
    if not GOOGLE_API_KEY:
        return None
    q = urlencode({"address": addr, "key": GOOGLE_API_KEY, "region": "tw", "language": "zh-TW"})
    r = _get(f"https://maps.googleapis.com/maps/api/geocode/json?{q}")
    if r and r.ok:
        js = r.json()
        if js.get("status") == "OK" and js.get("results"):
            loc = js["results"][0]["geometry"]["location"]
            return float(loc["lat"]), float(loc["lng"])
    return None

def geocode_best_effort(addr: str) -> Tuple[Optional[float], Optional[float], List[str]]:
    """目前只用 Google，之後可加 Mapbox/OSM"""
    latlng = geocode_google(addr)
    if latlng:
        return latlng[0], latlng[1], []
    return None, None, ["geocode:google failed"]

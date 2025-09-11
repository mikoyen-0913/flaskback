# tool/weather_from_rest.py
import os
import requests
import certifi
from typing import List, Dict, Optional

CWA_API_KEY = os.getenv("CWA_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ALLOW_INSECURE = os.getenv("ALLOW_INSECURE_WEATHER", "0") == "1"
CA_PATH = os.getenv("SSL_CERT_FILE") or certifi.where()

def _reverse_city_from_google(lat: float, lon: float) -> Optional[str]:
    if not GOOGLE_API_KEY:
        return None
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "latlng": f"{lat},{lon}",
        "language": "zh-TW",
        "region": "tw",
        "key": GOOGLE_API_KEY,
    }
    r = requests.get(url, params=params, timeout=8, verify=CA_PATH)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "OK" or not data.get("results"):
        return None

    # level_1 (縣市) 為主，沒有再退 level_2
    for comp in data["results"][0].get("address_components", []):
        if "administrative_area_level_1" in comp.get("types", []):
            name = comp.get("long_name") or comp.get("short_name")
            return name.replace("台", "臺") if name else None
    for comp in data["results"][0].get("address_components", []):
        if "administrative_area_level_2" in comp.get("types", []):
            name = comp.get("long_name") or comp.get("short_name")
            return name.replace("台", "臺") if name else None
    return None

def _parse_fc0032_001(j: dict) -> List[Dict[str, float]]:
    records = j.get("records", {})
    locations = records.get("location", [])
    if not locations:
        return []
    loc = locations[0]
    wx_elements = {e["elementName"]: e for e in loc.get("weatherElement", []) if "elementName" in e}

    def series(name):
        return wx_elements.get(name, {}).get("time", []) if wx_elements.get(name) else []

    pop12 = series("PoP12h")
    minT  = series("MinT")
    maxT  = series("MaxT")

    out: List[Dict[str, float]] = []
    n = min(3, len(pop12), len(minT), len(maxT))
    for i in range(n):
        try:
            p = float(pop12[i]["parameter"]["parameterName"])
        except Exception:
            p = 0.0
        try:
            tmin = float(minT[i]["parameter"]["parameterName"])
            tmax = float(maxT[i]["parameter"]["parameterName"])
            t = (tmin + tmax) / 2.0
        except Exception:
            t = 25.0
        out.append({"rainfall": round(p, 1), "temperature": round(t, 1)})
    return out

def _fetch_cwa_rest(city: str) -> dict:
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {
        "Authorization": CWA_API_KEY,
        "locationName": city,
        "format": "JSON",
    }
    headers = {"Accept": "application/json"}

    # ① 正常路徑：使用 certifi 憑證庫
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10, verify=CA_PATH)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.SSLError as ssl_err:
        # ② 備援路徑（可選）：允許不驗證 SSL 只為了取 CWA 這支，避免整個功能掛掉
        if ALLOW_INSECURE:
            print(f"⚠️ CWA SSL 驗證失敗（{ssl_err}），以 verify=False 暫時取代（僅此請求）。")
            resp = requests.get(url, params=params, headers=headers, timeout=10, verify=False)
            resp.raise_for_status()
            return resp.json()
        # 不允許不驗證時，直接往上拋
        raise

def fetch_weather_from_rest(lat: float, lon: float) -> Optional[List[Dict[str, float]]]:
    if not CWA_API_KEY:
        print("❌ 缺少 CWA_API_KEY 環境變數")
        return None

    city = _reverse_city_from_google(lat, lon) or "臺北市"
    print(f"🌐 CWA REST 以城市：{city}")

    j = _fetch_cwa_rest(city)
    items = _parse_fc0032_001(j)
    if not items:
        print("⚠️ CWA REST 解析不到有效資料")
        return None
    print(f"🌤️ CWA REST（{city}）→ {items}")
    return items
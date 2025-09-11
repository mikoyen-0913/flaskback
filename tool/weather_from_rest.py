# tool/weather_from_rest.py
import os
import requests
import certifi
from typing import List, Dict, Optional

CWA_API_KEY = os.getenv("CWA_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

def _reverse_city_from_google(lat: float, lon: float) -> Optional[str]:
    """
    用 Google Geocoding 由座標取行政區，回傳 CWA 需要的「縣市全名」（如：臺北市、新北市、桃園市）
    """
    if not GOOGLE_API_KEY:
        return None
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "latlng": f"{lat},{lon}",
        "language": "zh-TW",
        "region": "tw",
        "key": GOOGLE_API_KEY,
    }
    r = requests.get(url, params=params, timeout=8, verify=certifi.where())
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "OK" or not data.get("results"):
        return None

    # 在 address_components 裡找層級為 'administrative_area_level_1'（多半是「臺北市」等）
    for comp in data["results"][0].get("address_components", []):
        if "administrative_area_level_1" in comp.get("types", []):
            name = comp.get("long_name") or comp.get("short_name")
            # 兼容「台北市/臺北市」→ CWA 用「臺」
            return name.replace("台", "臺") if name else None
    # 如果沒抓到 level_1，就再嘗試 level_2（縣市級可能在這）
    for comp in data["results"][0].get("address_components", []):
        if "administrative_area_level_2" in comp.get("types", []):
            name = comp.get("long_name") or comp.get("short_name")
            return name.replace("台", "臺") if name else None
    return None

def _parse_fc0032_001(j: dict) -> List[Dict[str, float]]:
    """
    解析 F-C0032-001 結構，輸出最多 3 筆
    temperature: 取 MinT/MaxT 平均
    rainfall:    用 PoP12h (降雨機率 %)
    """
    records = j.get("records", {})
    locations = records.get("location", [])
    if not locations:
        return []

    loc = locations[0]  # 已用 locationName 篩到單一縣市
    # 重要天氣元素
    wx_elements = {e["elementName"]: e for e in loc.get("weatherElement", []) if "elementName" in e}

    def series(name):
        return wx_elements.get(name, {}).get("time", []) if wx_elements.get(name) else []

    pop12 = series("PoP12h")   # 降雨機率 %
    minT = series("MinT")
    maxT = series("MaxT")

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

def fetch_weather_from_rest(lat: float, lon: float) -> Optional[List[Dict[str, float]]]:
    """
    先把座標反解出「縣市」，再打 CWA F-C0032-001 取 36 小時預報，回傳 3 筆
    """
    if not CWA_API_KEY:
        print("❌ 缺少 CWA_API_KEY 環境變數")
        return None

    city = _reverse_city_from_google(lat, lon)
    if not city:
        print("⚠️ 由座標無法取到縣市，使用預設：臺北市")
        city = "臺北市"

    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {
        "Authorization": CWA_API_KEY,
        "locationName": city,
    }
    r = requests.get(url, params=params, timeout=10, verify=certifi.where())
    r.raise_for_status()
    j = r.json()

    items = _parse_fc0032_001(j)
    if not items:
        print("⚠️ CWA REST 解析不到有效資料，回傳 None")
        return None

    print(f"🌤️ CWA REST（{city}）→", items)
    return items

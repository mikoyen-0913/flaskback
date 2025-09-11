# tool/weather_from_rest.py
import os
import requests
import certifi
import urllib3
from typing import List, Dict, Optional

CWA_API_KEY = os.getenv("CWA_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ALLOW_INSECURE = os.getenv("ALLOW_INSECURE_WEATHER", "0") == "1"
CA_PATH = os.getenv("SSL_CERT_FILE") or certifi.where()

if ALLOW_INSECURE:
    # 只在我們允許不驗證時，關閉 urllib3 的警告（避免 log 轟炸）
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

    # level_1 > level_2
    for comp in data["results"][0].get("address_components", []):
        if "administrative_area_level_1" in comp.get("types", []):
            name = comp.get("long_name") or comp.get("short_name")
            return name.replace("台", "臺") if name else None
    for comp in data["results"][0].get("address_components", []):
        if "administrative_area_level_2" in comp.get("types", []):
            name = comp.get("long_name") or comp.get("short_name")
            return name.replace("台", "臺") if name else None
    return None

def _parse_fc0032_001_for_city(j: dict, want_city: str) -> List[Dict[str, float]]:
    """
    從 F-C0032-001 的回應中，挑出指定城市的資料（若伺服器沒過濾成功，就改用本地比對）。
    """
    records = j.get("records", {})
    locations = records.get("location", [])
    if not locations:
        return []

    # 先找完全相等，再退而求其次「包含」
    match = None
    for loc in locations:
        if loc.get("locationName") == want_city:
            match = loc
            break
    if match is None:
        for loc in locations:
            name = loc.get("locationName", "")
            if want_city in name or name in want_city:
                match = loc
                break
    if match is None:
        # 找不到就拿第一個，但外面會警告
        match = locations[0]

    wx_elements = {e["elementName"]: e for e in match.get("weatherElement", []) if "elementName" in e}
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

def _http_get(url: str, params: dict, headers: dict):
    """先用 verify=CA_PATH，失敗且允許時，用 verify=False 重試一次。"""
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10, verify=CA_PATH)
        resp.raise_for_status()
        return resp
    except requests.exceptions.SSLError as ssl_err:
        if ALLOW_INSECURE:
            print(f"⚠️ CWA SSL 驗證失敗（{ssl_err}），以 verify=False 暫時取代（僅此請求）。")
            resp = requests.get(url, params=params, headers=headers, timeout=10, verify=False)
            resp.raise_for_status()
            return resp
        raise

def _fetch_cwa_fc0032(city: Optional[str]) -> dict:
    """
    先嘗試用 locationName=city（若有給），抓不到就不帶 locationName 再抓一次。
    """
    base = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    headers = {"Accept": "application/json", "User-Agent": "yaoyao-backend/1.0"}

    # ① 帶 city 過濾
    if city:
        params = {"Authorization": CWA_API_KEY, "locationName": city, "format": "JSON"}
        resp = _http_get(base, params, headers)
        try:
            j = resp.json()
        except Exception:
            print(f"⚠️ CWA 回應非 JSON（帶 city），前 300 字：{resp.text[:300]}")
            j = {}
        locs = j.get("records", {}).get("location", [])
        if locs:
            return j
        # 無資料 → 落入 ②

    # ② 不帶 city，抓全縣市，回來再本地挑
    params = {"Authorization": CWA_API_KEY, "format": "JSON"}
    resp = _http_get(base, params, headers)
    try:
        j = resp.json()
    except Exception:
        print(f"⚠️ CWA 回應非 JSON（不帶 city），前 300 字：{resp.text[:300]}")
        j = {}
    return j

def fetch_weather_from_rest(lat: float, lon: float) -> Optional[List[Dict[str, float]]]:
    if not CWA_API_KEY:
        print("❌ 缺少 CWA_API_KEY 環境變數")
        return None

    city = _reverse_city_from_google(lat, lon) or "臺北市"
    print(f"🌐 CWA REST 以城市：{city}")

    j = _fetch_cwa_fc0032(city)
    items = _parse_fc0032_001_for_city(j, city)

    if not items:
        # 額外除錯訊息，幫你肉眼確認是不是格式不對
        try:
            rec = j.get("records", {})
            locs = rec.get("location", [])
            print(f"⚠️ CWA REST 解析不到有效資料；records.location 長度：{len(locs)}")
            if isinstance(locs, list) and locs:
                names = [x.get("locationName") for x in locs[:5]]
                print(f"⚠️ 前幾個 locationName：{names}")
        except Exception:
            pass
        return None

    print(f"🌤️ CWA REST（{city}）→ {items}")
    return items

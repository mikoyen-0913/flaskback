# tool/weather_from_rest.py
import os
import requests
import certifi
import urllib3
from typing import List, Dict, Optional

CWA_API_KEY = "CWA-6FAB80A3-75EF-4555-86C3-A026F8F0E564"
#os.getenv("CWA_API_KEY")
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
    從 F-C0032-001 回應中挑出 want_city 的資料，並兼容：
    - PoP12h 或 PoP 兩種名稱
    - 數值可能在 time[i].parameter.parameterName 或 time[i].elementValue[0].value
    - 各時間序列長度不一致時，盡量合併可用資訊
    """
    def _first(lst):
        return lst[0] if isinstance(lst, list) and lst else None

    records = j.get("records") or {}
    locations = records.get("location") or []
    if not locations:
        return []

    # 先精準比對，再包含比對
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
        match = locations[0]

    # 轉成 {elementName: time[]} 映射
    wx_elements = {}
    for e in match.get("weatherElement", []):
        name = e.get("elementName")
        if not name:
            continue
        wx_elements[name] = e.get("time") or []

    # 可能名稱：PoP12h 或 PoP
    pop_series  = wx_elements.get("PoP12h") or wx_elements.get("PoP") or []
    minT_series = wx_elements.get("MinT")   or []
    maxT_series = wx_elements.get("MaxT")   or []

    # 讀取每個 time block 的數值（相容兩種資料結構）
    def _read_value(block) -> Optional[float]:
        if not isinstance(block, dict):
            return None
        # 結構 1：time.parameter.parameterName
        p = block.get("parameter")
        if isinstance(p, dict):
            val = p.get("parameterName") or p.get("parameterValue")
            if val not in (None, ""):
                try:
                    return float(val)
                except Exception:
                    pass
        # 結構 2：time.elementValue[0].value
        ev = _first(block.get("elementValue"))
        if isinstance(ev, dict):
            val = ev.get("value")
            if val not in (None, ""):
                try:
                    return float(val)
                except Exception:
                    pass
        return None

    # 取可用長度（最多取 3 段，對齊方式：能取到就取，取不到就用前後補）
    L = max(len(pop_series), len(minT_series), len(maxT_series), 3)
    L = min(L, 3)

    out: List[Dict[str, float]] = []
    last_pop = 0.0
    last_t   = 25.0

    for i in range(L):
        # 降雨機率
        pop_block = pop_series[i] if i < len(pop_series) else None
        pop = _read_value(pop_block)
        if pop is None:
            pop = last_pop  # 補前值
        # 溫度（取 Min/Max 平均，缺一就用另一個）
        min_block = minT_series[i] if i < len(minT_series) else None
        max_block = maxT_series[i] if i < len(maxT_series) else None
        tmin = _read_value(min_block)
        tmax = _read_value(max_block)
        if tmin is None and tmax is None:
            t = last_t
        elif tmin is None:
            t = float(tmax)
        elif tmax is None:
            t = float(tmin)
        else:
            t = (float(tmin) + float(tmax)) / 2.0

        last_pop = float(pop if pop is not None else 0.0)
        last_t   = float(t)

        out.append({
            "rainfall": round(last_pop, 1),
            "temperature": round(last_t, 1),
        })

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

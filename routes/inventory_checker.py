# routes/inventory_checker.py
from flask import Blueprint, request, jsonify
from routes.auth import token_required

# --- 天氣改用 REST 版 ---
try:
    from tool.weather_from_rest import fetch_weather_from_rest
except Exception:
    from weather_from_rest import fetch_weather_from_rest  # 後備匯入路徑

from tool.lstm_predict_all import forecast_next_sales, load_models_and_data
from tool.firebase_fetcher import fetch_recipes
from tool.ingredient_demand import calculate_total_demand

from firebase_config import db

import os
import requests
import traceback
from datetime import datetime, timedelta, timezone, date
from typing import Any, Dict, List, Optional, Tuple


inventory_bp = Blueprint("inventory", __name__)

# -------------------------------
# 地址 → GPS（Google → OSM fallback）
# -------------------------------
def geocode_address(address: str) -> Tuple[float, float]:
    """
    優先用 Google Geocoding；失敗才退回 OpenStreetMap。
    需在環境變數設定 GOOGLE_API_KEY。
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key:
        try:
            g_url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "address": address,
                "region": "tw",
                "language": "zh-TW",
                "key": api_key,
            }
            r = requests.get(g_url, params=params, timeout=8)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "OK" and data.get("results"):
                loc = data["results"][0]["geometry"]["location"]
                lat, lon = float(loc["lat"]), float(loc["lng"])
                print(f"📍(Google) 地址轉換成功：{address} → ({lat}, {lon})")
                return lat, lon
            else:
                print(f"⚠️(Google) geocode 失敗：{data.get('status')}")
        except Exception as e:
            print(f"❌(Google) 地址轉換例外：{e}")

    # --- Fallback：OpenStreetMap Nominatim ---
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": address,
            "format": "json",
            "limit": 1,
            "countrycodes": "tw",
            "accept-language": "zh-TW",
        }
        r = requests.get(
            url,
            params=params,
            headers={"User-Agent": "yaoyao-backend/1.0"},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            print(f"📍(OSM) 地址轉換成功：{address} → ({lat}, {lon})")
            return lat, lon
    except Exception as e:
        print(f"❌(OSM) 地址轉換失敗：{e}")

    # 兩者都失敗：給台北市政府附近座標，避免整個流程中斷
    return 25.0375, 121.5637


# -------------------------------
# 時區/日期處理
# -------------------------------
def taipei_today() -> date:
    return (datetime.utcnow() + timedelta(hours=8)).date()


def parse_to_date(raw: Any) -> Optional[date]:
    """相容 datetime/date/字串/epoch/Firestore Timestamp(dict)."""
    if raw is None:
        return None

    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()

    if isinstance(raw, dict):
        sec = raw.get("_seconds") or raw.get("seconds")
        if sec is not None:
            if sec > 1e12:
                sec = sec / 1000.0
            return datetime.fromtimestamp(sec, tz=timezone.utc).date()
        if "value" in raw:
            return parse_to_date(raw["value"])

    if isinstance(raw, (int, float)):
        sec = raw if raw <= 1e12 else raw / 1000.0
        return datetime.fromtimestamp(sec, tz=timezone.utc).date()

    if isinstance(raw, str):
        s = raw.strip().replace("/", "-")
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(s).date()
            except Exception:
                return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    return None


def earliest_expiry_info(inv_entry: Any) -> Tuple[Optional[date], Optional[int]]:
    """
    inv_entry 可能是：
      - dict：{"quantity": 100, "unit": "克", "expiration_date": "2025-07-21"}（✅ 單層）
      - list：多批次 [{"quantity": 50, "unit": "...", "expiration_date": "..."}, ...]
    回傳：(最早日期, 與今天相差天數)
    """
    today = taipei_today()

    DATE_KEYS: Tuple[str, ...] = (
        "expiration_date", "expiry_date", "expire_date", "expire_on",
        "best_before", "best_before_date", "valid_until", "date",
        "expiration", "expirationDate", "validUntil", "exp", "expiry",
        "shelf_life",
        "保存期限", "到期日", "效期", "效期日", "效期至", "保存期限日", "保質期",
    )

    def pick_date_from_dict(d: Dict[str, Any]) -> Optional[date]:
        for k in DATE_KEYS:
            if k in d and d[k]:
                dt = parse_to_date(d[k])
                if dt:
                    return dt
        return None

    if isinstance(inv_entry, dict):
        dt = pick_date_from_dict(inv_entry)
        if dt:
            return dt, (dt - today).days

    if isinstance(inv_entry, list):
        dates: List[date] = []
        for batch in inv_entry:
            if isinstance(batch, dict):
                dt = pick_date_from_dict(batch)
                if dt:
                    dates.append(dt)
        if dates:
            earliest = min(dates)
            return earliest, (earliest - today).days

    return None, None


# -------------------------------
# ✅ Firestore 直接抓批次總庫存（in_use + unused）
# -------------------------------
def fetch_ingredient_inventory_batches(store_name: str) -> Dict[str, Dict[str, Any]]:
    """
    回傳格式維持『單層 dict』，讓後面的程式不用大改：
      inventory["SUGAR"] = {"quantity": 120, "unit": "克", "expiration_date": "2025-12-31"}

    計算規則（你要的簡單版）：
      - 可用庫存 = sum(batches where status in ["in_use","unused"])
      - 最早效期 = 在上述可用批次中，挑最早的 expiration_date
      - 如果某食材沒有 batches（舊資料），fallback 用父文件 quantity/expiration_date
    """
    inv: Dict[str, Dict[str, Any]] = {}

    ingredients = (
        db.collection("stores")
          .document(store_name)
          .collection("ingredients")
          .stream()
    )

    for ing_doc in ingredients:
        ing = ing_doc.to_dict() or {}
        name = (ing.get("name") or "").strip()
        if not name:
            continue

        key = name.upper()
        unit = ing.get("unit")

        # 1) 優先看 batches（新結構）
        batches_col = ing_doc.reference.collection("batches")

        total = 0.0
        earliest: Optional[date] = None
        has_any_batch = False

        try:
            # Firestore 沒有 in() 在所有 SDK 都一致保證，這裡用兩次查詢最穩
            for st in ("in_use", "unused"):
                for b in batches_col.where("status", "==", st).stream():
                    has_any_batch = True
                    bd = b.to_dict() or {}
                    qty = float(bd.get("quantity", 0) or 0)
                    if qty > 0:
                        total += qty

                    exp = parse_to_date(bd.get("expiration_date"))
                    if exp:
                        earliest = exp if (earliest is None or exp < earliest) else earliest
        except Exception:
            # 批次讀取失敗：退回父文件
            has_any_batch = False

        # 2) 若沒有任何 batch，就 fallback 父文件（支援舊資料）
        if not has_any_batch:
            total = float(ing.get("quantity", 0) or 0)
            earliest = parse_to_date(ing.get("expiration_date"))

        payload: Dict[str, Any] = {"quantity": float(total), "unit": unit}
        if earliest:
            payload["expiration_date"] = earliest.isoformat()

        inv[key] = payload

    return inv


# -------------------------------
# 主 API：檢查缺料 + 效期
# -------------------------------
@inventory_bp.route("/check_inventory", methods=["POST"])
@token_required
def check_inventory():
    print("🔍 使用者資訊：", request.user)
    try:
        address = request.user.get("address")
        store_name = request.user.get("store_name")
        if not address:
            return jsonify({"error": "使用者未設定地址"}), 400
        if not store_name:
            return jsonify({"error": "使用者未設定店家 store_name"}), 400

        # 1) GPS
        lat, lon = geocode_address(address)

        # 2) 天氣（給需求預測用）
        forecast_data = fetch_weather_from_rest(lat, lon)
        if not forecast_data:
            return jsonify({"error": "氣象資料取得失敗"}), 400

        # 3) 載入模型與資料
        models, scalers, pivot_df, flavors = load_models_and_data()

        # 4) 預測未來需求（把 3 個時段的預測量加總）
        predicted_sales: Dict[str, float] = {}
        for flavor in flavors:
            model = models.get(flavor)
            scaler = scalers.get(flavor)
            if model and scaler:
                y_pred = forecast_next_sales(flavor, pivot_df, model, scaler, forecast_data)
                predicted_sales[flavor] = float(sum(y_pred))

        # 5) ✅ 取庫存/食譜 + 計算需求（庫存改成 batches 總量）
        inventory = fetch_ingredient_inventory_batches(store_name)
        recipes = fetch_recipes()
        demand = calculate_total_demand(predicted_sales, recipes)

        # 6) 組報告（含效期）
        EXPIRY_THRESHOLD_DAYS = 3
        expiring_soon_list: List[Dict[str, Any]] = []
        shortage_report: Dict[str, Any] = {}

        for ingredient, info in demand.items():
            required = float(info["total"])
            unit = info["unit"]
            name = ingredient.upper()
            inv_entry = inventory.get(name, {"quantity": 0, "unit": unit})

            # 數量合計 + 單位
            if isinstance(inv_entry, dict) and "quantity" in inv_entry:
                available = float(inv_entry.get("quantity", 0))
                available_unit = inv_entry.get("unit", unit)
            else:
                # 這邊通常不會再遇到 list（因為我們回傳單層），保留兼容
                available = 0.0
                available_unit = unit
                if isinstance(inv_entry, list):
                    for batch in inv_entry:
                        if isinstance(batch, dict):
                            available += float(batch.get("quantity", 0))
                            if "unit" in batch:
                                available_unit = batch["unit"]

            entry = {"required": required, "available": available, "unit": unit}

            # 狀態
            if unit != available_unit:
                entry["status"] = "單位不一致"
            elif required > available:
                entry["status"] = "缺料"
                entry["shortage"] = required - available
            else:
                entry["status"] = "足夠"

            # ✅ 效期（任何狀態都檢查；包含已過期）
            expire_on, days_left = earliest_expiry_info(inv_entry)
            if expire_on is not None and days_left is not None:
                entry["expire_on"] = expire_on.isoformat()
                entry["days_left"] = days_left
                if days_left <= EXPIRY_THRESHOLD_DAYS:
                    entry["expire_status"] = "已過期" if days_left < 0 else "即將過期"
                    expiring_soon_list.append({
                        "ingredient": name,
                        "expire_on": expire_on.isoformat(),
                        "days_left": days_left,
                        "available": available,
                        "unit": unit,
                    })

            shortage_report[name] = entry

        # 需求沒用到但庫存有的（也要顯示＋檢查效期）
        for name, inv_entry in inventory.items():
            if name in shortage_report:
                continue

            available = float(inv_entry.get("quantity", 0) or 0) if isinstance(inv_entry, dict) else 0.0
            unit = inv_entry.get("unit") if isinstance(inv_entry, dict) else None

            entry = {"status": "未使用", "required": 0.0, "available": available, "unit": unit}
            expire_on, days_left = earliest_expiry_info(inv_entry)
            if expire_on is not None and days_left is not None:
                entry["expire_on"] = expire_on.isoformat()
                entry["days_left"] = days_left
                if days_left <= EXPIRY_THRESHOLD_DAYS:
                    entry["expire_status"] = "已過期" if days_left < 0 else "即將過期"
                    expiring_soon_list.append({
                        "ingredient": name,
                        "expire_on": expire_on.isoformat(),
                        "days_left": days_left,
                        "available": available,
                        "unit": unit,
                    })

            shortage_report[name] = entry

        return jsonify({
            "forecasted_demand": demand,
            "inventory": inventory,
            "shortage_report": shortage_report,
            "expiring_soon": {
                "title": "以下食材即將到期 / 已過期，請盡速使用或下架",
                "threshold_days": EXPIRY_THRESHOLD_DAYS,
                "items": sorted(expiring_soon_list, key=lambda x: (x["days_left"], x["ingredient"]))
            },
            "today": taipei_today().isoformat(),
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

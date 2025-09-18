# routes/superadmin.py
from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required
from datetime import datetime, timedelta, date
import calendar
import requests
from collections import defaultdict
from google.cloud import firestore
import json
from google.cloud.firestore_v1 import FieldFilter
import os

superadmin_bp = Blueprint("superadmin", __name__)

# ------------- 共用工具函式 -------------

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _get_ing_doc_ref(store: str, ingredient_id: str | None, ingredient_name: str | None):
    col = db.collection("stores").document(store).collection("ingredients")
    if ingredient_id:
        return col.document(ingredient_id)
    if ingredient_name:
        # 名稱對應（大小寫不敏感，建議你日後在食材文件儲存 name_lower 以利索引）
        q = col.where("name", "==", ingredient_name).limit(1).stream()
        doc = next(q, None)
        if doc:
            return col.document(doc.id)
    return None

def _ymd(dt: date) -> str:
    """date -> 'YYYYMMDD'"""
    return f"{dt.year}{dt.month:02d}{dt.day:02d}"

def _iter_days(start_dt: date, end_dt_exclusive: date):
    """yield date from start_dt (inclusive) to end_dt_exclusive (exclusive)"""
    cur = start_dt
    while cur < end_dt_exclusive:
        yield cur
        cur += timedelta(days=1)

def _month_range(year: int, month: int):
    """return (start_date, end_date_exclusive) of the month"""
    start_dt = date(year, month, 1)
    if month == 12:
        end_dt = date(year + 1, 1, 1)
    else:
        end_dt = date(year, month + 1, 1)
    return start_dt, end_dt

def _get_daily_summary(store_name: str, dt: date):
    """讀取單日 summary（可能不存在）"""
    doc_ref = (
        db.collection("stores").document(store_name)
          .collection("dates").document(_ymd(dt))
          .collection("daily_summary").document("summary")
    )
    snap = doc_ref.get()
    return snap.to_dict() if snap.exists else None

def _ym_keys_for_year(year: int):
    return [f"{year}{m:02d}" for m in range(1, 13)]

def _monthly_doc_ref(store: str, yyyymm: str):
    return (db.collection("stores").document(store)
              .collection("months").document(yyyymm)
              .collection("monthly_summary").document("summary"))

def _sum_store_revenue_between(store_name: str, start_dt: date, end_dt_exclusive: date) -> int:
    """用 daily_summary 累加指定區間的 revenue"""
    total = 0
    for d in _iter_days(start_dt, end_dt_exclusive):
        data = _get_daily_summary(store_name, d)
        if data:
            total += int(data.get("revenue", 0))
    return total

def _sum_store_orders_between(store_name: str, start_dt: date, end_dt_exclusive: date) -> int:
    """用 daily_summary 累加指定區間的 orders_count"""
    total = 0
    for d in _iter_days(start_dt, end_dt_exclusive):
        data = _get_daily_summary(store_name, d)
        if data:
            total += int(data.get("orders_count", 0))
    return total

def _sum_store_flavor_counts_in_month(store_name: str, year: int, month: int):
    """回傳 (counts_map, labels_map)，用當月每天的 flavor_counts 相加，labels 以最後一次出現為準"""
    start_dt, end_dt = _month_range(year, month)
    counts = defaultdict(int)
    labels = {}
    for d in _iter_days(start_dt, end_dt):
        data = _get_daily_summary(store_name, d)
        if not data:
            continue
        fc = data.get("flavor_counts", {}) or {}
        for fid, cnt in fc.items():
            try:
                counts[fid] += int(cnt)
            except Exception:
                pass
        fl = data.get("flavor_labels", {}) or {}
        # 用最後一次看到的標籤覆蓋（通常一致）
        for fid, name in fl.items():
            labels[fid] = name
    return counts, labels

def _safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

# 固定菜單對照表（menu_id → 中文名稱）
MENU_ID_TO_NAME = {
    "3VfLsB3SiyuoTlM4GaJ": "巧克力",
    "6PRuy7bKGyLBm4KMEJL": "OREO鮮奶油",
    "77UxA3bJBWoFDqX5DzZY": "抹茶麻糬",
    "Dy0yTpP3UHolYm7B7cZcF": "紅豆",
    "GBaSoOy1xpfRe84hYrh": "可可布朗尼",
    "NfwKvNfnwxW1tKGb0oi": "珍珠鮮奶油",
    "QYo2B71FnXXcJiL1Si1Z": "奶油",
    "aWggBPCNenfGFq0f3zbq": "黑芝麻鮮奶油",
    "n4YigbOVv2YQpAi6t0BU": "花生",
    "sNk2RNReyIZz58vifCm": "紅豆麻糬",
}

# ------------- 地圖：地址轉經緯度 -------------

def geocode_address(address):
    """
    優先用 Google Geocoding；失敗再退回 OSM。
    回傳 (lat, lon)；若都失敗回 (None, None)
    """
    api_key = os.getenv("GOOGLE_API_KEY")

    # --- Google 優先 ---
    if api_key:
        try:
            g_url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "address": address,
                "region": "tw",
                "language": "zh-TW",
                "key": api_key,
            }
            resp = requests.get(g_url, params=params, timeout=8)
            resp.raise_for_status()
            j = resp.json()
            if j.get("status") == "OK" and j.get("results"):
                loc = j["results"][0]["geometry"]["location"]
                return float(loc["lat"]), float(loc["lng"])
            else:
                print(f"[geocode_address][Google] 失敗：{j.get('status')}")
        except Exception as e:
            print(f"[geocode_address][Google] 例外：{address} => {e}")

    # --- OSM fallback ---
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": address,
            "format": "json",
            "limit": 1,
            "countrycodes": "tw",
            "accept-language": "zh-TW",
        }
        headers = {"User-Agent": "yaoyao-superadmin-map"}
        resp = requests.get(url, params=params, headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return lat, lon
    except Exception as e:
        print(f"[geocode_address][OSM] 失敗：{address} => {e}")

    return None, None
# ------------- API 區 -------------

@superadmin_bp.route("/get_all_store_revenue", methods=["GET"])
@token_required
def get_all_store_revenue():
    """
    各分店營收折線圖
    range: 7days / month / year
    - 7days：回傳最近 7 天每天營收
    - month：?month=YYYY-MM，回傳當月每天營收
    - year：?year=YYYY，回傳每月總營收
    """
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    store_names = user.get("store_ids", [])
    range_type = request.args.get("range", "7days").lower()
    today = date.today()
    result = []

    if range_type == "7days":
        start_dt = today - timedelta(days=6)
        labels = [(start_dt + timedelta(days=i)).strftime("%m/%d") for i in range(7)]
        for store in store_names:
            revenues = []
            for d in _iter_days(start_dt, today + timedelta(days=1)):
                data = _get_daily_summary(store, d)
                revenues.append(_safe_int(data.get("revenue")) if data else 0)
            result.append({"store_name": store, "dates": labels, "revenues": revenues})

    elif range_type == "month":
        month_str = request.args.get("month")
        if not month_str:
            return jsonify({"error": "請提供月份，格式 YYYY-MM"}), 400
        try:
            y, m = map(int, month_str.split("-"))
        except Exception:
            return jsonify({"error": "月份格式錯誤，需為 YYYY-MM"}), 400

        start_dt, end_dt = _month_range(y, m)
        # 若查詢當月，顯示到今天；否則顯示到月底
        end_for_label = min(end_dt, today + timedelta(days=1)) if (y == today.year and m == today.month) else end_dt
        labels = []
        d = start_dt
        while d < end_for_label:
            labels.append(d.strftime("%m/%d"))
            d += timedelta(days=1)

        for store in store_names:
            revenues = []
            d = start_dt
            while d < end_for_label:
                data = _get_daily_summary(store, d)
                revenues.append(_safe_int(data.get("revenue")) if data else 0)
                d += timedelta(days=1)
            result.append({"store_name": store, "dates": labels, "revenues": revenues})

    elif range_type == "year":
        y = int(request.args.get("year", today.year))

        # 固定 12 個月份鍵與對應的標籤
        ym_keys = _ym_keys_for_year(y)                    # ["202501", …, "202512"]
        labels = [f"{int(ym[4:6])}月" for ym in ym_keys]  # ["1月", …, "12月"]

        for store in store_names:
            # 批次抓該店 12 個 monthly_summary
            refs = [_monthly_doc_ref(store, ym) for ym in ym_keys]
            docs = db.get_all(refs)

            # 建 monthly -> revenue 對照，有文件就讀值，沒有就缺席（之後補 0）
            month_to_rev = {}
            for doc in docs:
                parts = doc.reference.path.split("/")
                yyyymm = parts[parts.index("months") + 1] if "months" in parts else None
                if not yyyymm:
                    continue
                if doc.exists:
                    data = doc.to_dict() or {}
                    rev = float(data.get("total_revenue", data.get("revenue", 0)) or 0)
                    month_to_rev[yyyymm] = round(rev, 2)

            # 依固定 12 個月份輸出，缺的就填 0
            revenues = [month_to_rev.get(ym, 0) for ym in ym_keys]
            result.append({"store_name": store, "dates": labels, "revenues": revenues})

    else:
        return jsonify({"error": "range 參數錯誤，允許值：7days / month / year"}), 400

    return jsonify(result), 200

@superadmin_bp.route("/get_store_flavor_sales", methods=["GET"])
@token_required
def get_store_flavor_sales():
    """
    圓餅圖口味統計（每家店各自回傳）
    參數：month=YYYY-MM
    回傳：{ store_name: [{name, value}, ...], ... }
    """
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    # 解析參數 month
    month_str = request.args.get("month")
    if not month_str:
        return jsonify({"error": "請提供月份，格式 YYYY-MM"}), 400

    try:
        y, m = map(int, month_str.split("-"))
    except Exception:
        return jsonify({"error": "月份格式錯誤，需為 YYYY-MM"}), 400

    store_names = user.get("store_ids", [])
    result = {}

    for store in store_names:
        counts, labels = _sum_store_flavor_counts_in_month(store, y, m)
        pie = []

        for fid, qty in counts.items():
            # 嘗試用 labels 對應中文名稱；找不到就回傳 id
            name = labels.get(fid, fid)
            pie.append({
                "name": name,
                "value": int(qty)
            })

        result[store] = pie

    return jsonify(result), 200

@superadmin_bp.route("/get_summary_this_month", methods=["GET"])
@token_required
def get_summary_this_month():
    """
    本月總銷售額與訂單數（彙總所有可管理的分店）
    """
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    store_names = user.get("store_ids", [])
    today = date.today()
    start_dt, end_dt = _month_range(today.year, today.month)

    total_sales = 0
    total_orders = 0
    for store in store_names:
        total_sales += _sum_store_revenue_between(store, start_dt, end_dt)
        total_orders += _sum_store_orders_between(store, start_dt, end_dt)

    return jsonify({"total_sales": int(total_sales), "total_orders": int(total_orders)}), 200

@superadmin_bp.route("/get_top_flavors", methods=["GET"])
@token_required
def get_top_flavors():
    """
    銷售排行榜（跨所有分店的口味總量 Top 10）
    參數：month=YYYY-MM
    """
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    month_str = request.args.get("month")
    if not month_str:
        return jsonify({"error": "請提供月份，格式 YYYY-MM"}), 400

    try:
        y, m = map(int, month_str.split("-"))
    except Exception:
        return jsonify({"error": "月份格式錯誤，需為 YYYY-MM"}), 400

    store_names = user.get("store_ids", [])
    total_counts = defaultdict(int)
    latest_labels = {}

    for store in store_names:
        counts, labels = _sum_store_flavor_counts_in_month(store, y, m)
        for fid, qty in counts.items():
            total_counts[fid] += int(qty)
        latest_labels.update(labels)

    top10 = sorted(total_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    result = [{"name": latest_labels.get(fid, fid), "value": int(qty)} for fid, qty in top10]
    return jsonify(result), 200

@superadmin_bp.route("/get_my_stores", methods=["GET"])
@token_required
def get_my_stores():
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "僅限企業主使用"}), 403
    store_ids = user.get("store_ids", [])
    return jsonify({"stores": store_ids}), 200

@superadmin_bp.route("/get_inventory_by_store", methods=["GET"])
@token_required
def get_inventory_by_store():
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "僅限企業主使用"}), 403

    store_name = request.args.get("store")
    if not store_name:
        return jsonify({"error": "請提供 store 參數"}), 400

    try:
        ingredients_ref = db.collection("stores").document(store_name).collection("ingredients")
        snapshot = ingredients_ref.stream()
        inventory = [{"id": doc.id, **(doc.to_dict() or {})} for doc in snapshot]
        return jsonify({"store": store_name, "inventory": inventory}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@superadmin_bp.route("/get_store_revenue_rank", methods=["GET"])
@token_required
def get_store_revenue_rank():
    """
    分店營收排行榜（指定月份）
    參數：month=YYYY-MM
    """
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    month_str = request.args.get("month")
    if not month_str:
        return jsonify({"error": "請提供月份，格式 YYYY-MM"}), 400

    try:
        y, m = map(int, month_str.split("-"))
    except Exception:
        return jsonify({"error": "月份格式錯誤，需為 YYYY-MM"}), 400

    start_dt, end_dt = _month_range(y, m)
    store_names = user.get("store_ids", [])
    store_sales = []

    for store in store_names:
        total = _sum_store_revenue_between(store, start_dt, end_dt)
        store_sales.append({"store_name": store, "total_sales": int(total)})

    store_sales.sort(key=lambda x: x["total_sales"], reverse=True)
    return jsonify(store_sales), 200

@superadmin_bp.route("/get_store_locations", methods=["GET"])
@token_required
def get_store_locations():
    """
    superadmin 取得所有分店的地址、經緯度與（指定期間）營收總額
    參數：
      range = 7days / month / year（預設 month）
      month = YYYY-MM（當 range=month 時可傳，預設當月）
      year  = YYYY（當 range=year 時可傳，預設今年）
    """
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    rng = request.args.get("range", "month").lower()
    today = date.today()

    if rng == "7days":
        start_dt = today - timedelta(days=6)
        end_dt = today + timedelta(days=1)
    elif rng == "month":
        month_str = request.args.get("month")
        if month_str:
            try:
                y, m = map(int, month_str.split("-"))
            except Exception:
                return jsonify({"error": "月份格式錯誤，需為 YYYY-MM"}), 400
        else:
            y, m = today.year, today.month
        start_dt, end_dt = _month_range(y, m)
    elif rng == "year":
        y = int(request.args.get("year", today.year))
        start_dt = date(y, 1, 1)
        end_dt = date(y + 1, 1, 1)
    else:
        return jsonify({"error": "range 參數錯誤，允許值：7days / month / year"}), 400

    # 掃 users，挑出分店帳號（developer / staff）
    users_ref = db.collection("users")
    docs = users_ref.stream()

    store_list = []
    for doc in docs:
        data = doc.to_dict() or {}
        role = data.get("role", "")
        if role not in ["developer", "staff"]:
            continue

        store_name = data.get("store_name", "")
        address = data.get("address", "")

        revenue = 0
        if store_name:
            try:
                revenue = _sum_store_revenue_between(store_name, start_dt, end_dt)
            except Exception as e:
                print(f"[get_store_locations] 營收查詢失敗 {store_name}: {e}")

        lat, lon = geocode_address(address)

        store_list.append({
            "store_name": store_name,
            "address": address,
            "latitude": lat,
            "longitude": lon,
            "revenue": int(revenue),
        })

    return jsonify(store_list), 200

# ===== 調貨：CORS 預檢（/superadmin/transfer 用） =====

@superadmin_bp.route("/superadmin/transfer", methods=["OPTIONS"])
def superadmin_transfer_preflight():
    origin = request.headers.get("Origin", "*")
    resp = jsonify({})
    resp.status_code = 204
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp

# ===== 調貨：實作庫存轉移 & 紀錄 =====

@superadmin_bp.route("/superadmin/transfer_ingredient", methods=["POST"])
@token_required
def transfer_ingredient():
    user = request.user or {}
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    data = request.get_json(silent=True) or {}

    from_store = data.get("from_store")
    to_store   = data.get("to_store")
    qty        = data.get("quantity")
    unit       = data.get("unit")
    # 對應參數（任選其一組）
    from_ing_id = data.get("from_ingredient_id") or data.get("ingredient_id")
    to_ing_id   = data.get("to_ingredient_id")
    ing_name    = data.get("ingredient_name")

    # --- 基本驗證 ---
    if not from_store or not to_store:
        return jsonify({"error": "from_store / to_store 必填"}), 400
    try:
        qty = float(qty)
        if qty <= 0:
            raise ValueError()
    except Exception:
        return jsonify({"error": "quantity 必須是正數"}), 400
    if not unit:
        return jsonify({"error": "unit 必填"}), 400

    # --- 取得兩邊的 doc ref（支援 id 或 name 對應） ---
    from_ref = _get_ing_doc_ref(from_store, from_ing_id, ing_name)
    to_ref   = _get_ing_doc_ref(to_store,   to_ing_id,   ing_name)

    if not from_ref:
        return jsonify({"error": f"來源店找不到對應食材文件（store={from_store}, id={from_ing_id}, name={ing_name}）"}), 404
    if not to_ref:
        return jsonify({"error": f"目的店找不到對應食材文件（store={to_store}, id={to_ing_id}, name={ing_name}）"}), 404

    # --- Firestore 交易：原子讀寫與驗證 ---
    transaction = db.transaction()

    @firestore.transactional
    def _tx(transaction, from_ref, to_ref, qty, unit):
        from_snap = from_ref.get(transaction=transaction)
        to_snap   = to_ref.get(transaction=transaction)

        if not from_snap.exists:
            raise ValueError("來源店食材不存在")
        if not to_snap.exists:
            raise ValueError("目的店食材不存在")

        from_data = from_snap.to_dict() or {}
        to_data   = to_snap.to_dict() or {}

        # 單位一致性檢查
        from_unit = (from_data.get("unit") or "").strip().lower()
        to_unit   = (to_data.get("unit") or "").strip().lower()
        req_unit  = (unit or "").strip().lower()
        if from_unit != req_unit or to_unit != req_unit:
            raise ValueError(f"單位不一致：from={from_data.get('unit')}, to={to_data.get('unit')}, req={unit}")

        from_qty = float(from_data.get("quantity", 0))
        to_qty   = float(to_data.get("quantity", 0))
        if from_qty < qty:
            raise ValueError(f"庫存不足：來源可用 {from_qty} < 轉出 {qty}")

        transaction.update(from_ref, {"quantity": from_qty - qty})
        transaction.update(to_ref,   {"quantity": to_qty + qty})

    try:
        _tx(transaction, from_ref, to_ref, qty, unit)
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"交易失敗：{e}"}), 500

    # --- 成功後寫入交易紀錄 ---
    payload_str = json.dumps(data, ensure_ascii=False)
    log = {
        "created_at": firestore.SERVER_TIMESTAMP,
        "created_by": user.get("uid"),
        "from_store": from_store,
        "to_store": to_store,
        "from_ingredient_id": from_ing_id,
        "to_ingredient_id": to_ing_id,
        "ingredient_name": ing_name,
        "quantity": qty,
        "unit": unit,
        "payload_str": payload_str,
        "status": "success",
    }
    db.collection("transaction").add(log)

    return jsonify({"ok": True, "message": "調貨成功（已原子更新兩店庫存）"}), 200

# ===== 調貨紀錄：CORS 預檢 + 查詢 API =====

@superadmin_bp.route("/superadmin/transfer_logs", methods=["OPTIONS"])
def transfer_logs_preflight():
    """
    讓瀏覽器 CORS 預檢通過（與 /superadmin/transfer 的 OPTIONS 寫法一致）
    """
    origin = request.headers.get("Origin", "*")
    resp = jsonify({})
    resp.status_code = 204
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp  # 204 No Content


@superadmin_bp.route("/superadmin/transfer_logs", methods=["GET"])
@token_required
def get_transfer_logs():
    """
    取得調貨紀錄（transaction 集合）
    可選參數：
      from_store, to_store, ingredient（完整比對 ingredient_name）, limit(預設100, 最多500)
    """
    user = request.user or {}
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    from_store = request.args.get("from_store") or ""
    to_store   = request.args.get("to_store") or ""
    ingredient = request.args.get("ingredient") or ""
    try:
        limit = int(request.args.get("limit", 100))
    except Exception:
        limit = 100
    limit = max(1, min(limit, 500))

    try:
        q = db.collection("transaction")
        if from_store:
            q = q.where(filter=FieldFilter("from_store", "==", from_store))
        if to_store:
            q = q.where(filter=FieldFilter("to_store", "==", to_store))
        if ingredient:
            q = q.where(filter=FieldFilter("ingredient_name", "==", ingredient))

        # 不在 Firestore 端 order_by，避免複合索引需求/型別不一致導致 500
        q = q.limit(limit)

        rows = []
        for doc in q.stream():
            d = doc.to_dict() or {}
            ts = d.get("created_at")
            # 安全轉 ISO（可能是 Timestamp/字串/None）
            try:
                created_at = ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts is not None else None)
            except Exception:
                created_at = str(ts) if ts is not None else None

            rows.append({
                "id": doc.id,
                "created_at": created_at,
                "from_store": d.get("from_store"),
                "to_store": d.get("to_store"),
                "ingredient_name": d.get("ingredient_name"),
                "quantity": d.get("quantity"),
                "unit": d.get("unit"),
                "created_by": d.get("created_by"),
                "status": d.get("status"),
            })

        # Python 端安全排序（None 置底）
        rows.sort(key=lambda r: (r.get("created_at") is None, r.get("created_at")), reverse=True)

        resp = jsonify({"logs": rows})
        origin = request.headers.get("Origin", "*")
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        return resp, 200

    except Exception as e:
        return jsonify({"error": f"查詢失敗: {e}"}), 500
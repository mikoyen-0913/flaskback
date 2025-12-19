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
import types # 用於檢查 generator 類型

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

# ====== 批次庫存統計用：日期/數字工具 ======
def _to_float(x, default=0.0):
    try:
        if x is None:
            return default
        if isinstance(x, (int, float)):
            return float(x)
        return float(str(x).strip())
    except Exception:
        return default

def _parse_date(val):
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return datetime.strptime(val[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    if isinstance(val, dict):
        sec = val.get("_seconds") or val.get("seconds")
        if sec:
            return datetime.utcfromtimestamp(sec).date()
    return None

def _sum_available_and_earliest_exp(ing_ref, parent_data: dict):
    """
    回傳 (total_available, earliest_exp_date)
    total_available = sum(batches.status in ['in_use','unused'])
    earliest_exp_date = 上述可用批次中最早 expiration_date
    若沒有 batches（舊資料），fallback parent quantity/expiration_date
    """
    total = 0.0
    earliest = None

    try:
        batches_col = ing_ref.collection("batches")
        for st in ("in_use", "unused"):
            for b in batches_col.where("status", "==", st).stream():
                bd = b.to_dict() or {}
                q = _to_float(bd.get("quantity"), 0.0)
                if q > 0:
                    total += q
                exp = _parse_date(bd.get("expiration_date"))
                if exp:
                    earliest = exp if (earliest is None or exp < earliest) else earliest
        return total, earliest
    except Exception:
        # fallback：若 batches 讀不到，退回父文件
        q = _to_float(parent_data.get("quantity"), 0.0)
        exp = _parse_date(parent_data.get("expiration_date"))
        return q, exp

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
        ym_keys = _ym_keys_for_year(y)
        labels = [f"{int(ym[4:6])}月" for ym in ym_keys]

        for store in store_names:
            refs = [_monthly_doc_ref(store, ym) for ym in ym_keys]
            docs = db.get_all(refs)
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
            revenues = [month_to_rev.get(ym, 0) for ym in ym_keys]
            result.append({"store_name": store, "dates": labels, "revenues": revenues})
    else:
        return jsonify({"error": "range 參數錯誤，允許值：7days / month / year"}), 400

    return jsonify(result), 200

@superadmin_bp.route("/get_store_flavor_sales", methods=["GET"])
@token_required
def get_store_flavor_sales():
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
    result = {}

    for store in store_names:
        counts, labels = _sum_store_flavor_counts_in_month(store, y, m)
        pie = []
        for fid, qty in counts.items():
            name = labels.get(fid, fid)
            pie.append({"name": name, "value": int(qty)})
        result[store] = pie

    return jsonify(result), 200

@superadmin_bp.route("/get_summary_this_month", methods=["GET"])
@token_required
def get_summary_this_month():
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
        return jsonify({"error": "permission denied"}), 403

    store = request.args.get("store")
    if not store:
        return jsonify({"error": "store required"}), 400

    result = []
    ing_col = db.collection("stores").document(store).collection("ingredients")

    for doc in ing_col.stream():
        data = doc.to_dict() or {}
        ing_ref = doc.reference
        total = 0.0
        earliest = None
        try:
            batches = ing_ref.collection("batches")
            for st in ("in_use", "unused"):
                for b in batches.where("status", "==", st).stream():
                    bd = b.to_dict() or {}
                    qty = _to_float(bd.get("quantity"))
                    total += qty
                    exp = _parse_date(bd.get("expiration_date"))
                    if exp:
                        earliest = exp if earliest is None or exp < earliest else earliest
        except Exception:
            total = _to_float(data.get("quantity"))
            earliest = _parse_date(data.get("expiration_date"))

        data["quantity"] = int(round(total))
        data["expiration_date"] = earliest.isoformat() if earliest else None
        result.append({"id": doc.id, **data})

    return jsonify({"store": store, "inventory": result}), 200

@superadmin_bp.route("/get_store_revenue_rank", methods=["GET"])
@token_required
def get_store_revenue_rank():
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

# ===== ✅ 調貨：批次庫存版（修復 Generator 錯誤 + Transaction 寫入） =====
@superadmin_bp.route("/superadmin/transfer_ingredient", methods=["POST"])
@token_required
def transfer_ingredient():
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "permission denied"}), 403

    data = request.get_json() or {}

    from_store = data.get("from_store")
    to_store = data.get("to_store")
    qty = _to_float(data.get("quantity"))
    unit = data.get("unit")
    ing_name = data.get("ingredient_name")
    from_ing_id = data.get("from_ingredient_id")
    to_ing_id = data.get("to_ingredient_id")

    if qty <= 0:
        return jsonify({"error": "quantity must be positive"}), 400

    from_ref = _get_ing_doc_ref(from_store, from_ing_id, ing_name)
    to_ref = _get_ing_doc_ref(to_store, to_ing_id, ing_name)

    if not from_ref or not to_ref:
        return jsonify({"error": "ingredient not found"}), 404

    transaction = db.transaction()

    @firestore.transactional
    def _tx(tx):
        # [FIX] 安全讀取：若 tx.get() 返回 generator，則用 next() 取出 snapshot
        from_obj = tx.get(from_ref)
        if isinstance(from_obj, types.GeneratorType):
            from_snap = next(from_obj)
        else:
            from_snap = from_obj

        to_obj = tx.get(to_ref)
        if isinstance(to_obj, types.GeneratorType):
            to_snap = next(to_obj)
        else:
            to_snap = to_obj

        if not from_snap.exists or not to_snap.exists:
            raise ValueError("ingredient missing")

        to_data = to_snap.to_dict()

        # 找來源 in_use 批次
        inuse_docs = list(
            from_ref.collection("batches")
            .where("status", "==", "in_use")
            .limit(1)
            .stream(transaction=tx)
        )
        if not inuse_docs:
            raise ValueError("no in_use batch")

        b = inuse_docs[0]
        bd = b.to_dict()
        cur_qty = _to_float(bd.get("quantity"))

        if cur_qty < qty:
            raise ValueError("not enough stock")

        new_qty = cur_qty - qty

        # 更新來源
        tx.update(b.reference, {
            "quantity": new_qty,
            "status": "depleted" if new_qty == 0 else "in_use"
        })

        # 新增目的批次
        new_batch_ref = to_ref.collection("batches").document()
        tx.set(new_batch_ref, {
            "quantity": qty,
            "unit": unit,
            "status": "unused",
            "created_at": firestore.SERVER_TIMESTAMP,
            "expiration_date": bd.get("expiration_date"),
            "note": f"from {from_store}"
        })

        # 若目的沒有 active 批次 → 直接 in_use
        if not to_data.get("current_batch_id"):
            tx.update(new_batch_ref, {"status": "in_use"})
            tx.update(to_ref, {
                "current_batch_id": new_batch_ref.id,
                "quantity": qty,
                "status": "in_stock"
            })
        
        # [FIX] 寫入交易紀錄
        log_ref = db.collection("transaction").document()
        tx.set(log_ref, {
            "type": "transfer",
            "action": "transfer",
            "from_store": from_store,
            "to_store": to_store,
            "ingredient_name": ing_name,
            "quantity": qty,
            "unit": unit,
            "operator": user.get("uid"),
            "operator_name": user.get("name", "Superadmin"),
            "created_at": firestore.SERVER_TIMESTAMP,
            "status": "success"
        })

    try:
        _tx(transaction)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"transaction failed: {e}"}), 500

    return jsonify({"ok": True}), 200

@superadmin_bp.route("/superadmin/transfer_logs", methods=["OPTIONS"])
def transfer_logs_options():
    origin = request.headers.get("Origin", "*")
    resp = jsonify({})
    resp.status_code = 204
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp

@superadmin_bp.route("/superadmin/transfer_logs", methods=["GET"])
@token_required
def transfer_logs():
    user = request.user or {}
    if user.get("role") != "superadmin":
        return jsonify({"error": "僅限企業主使用"}), 403

    limit = request.args.get("limit", "100")
    try:
        limit = int(limit)
    except Exception:
        limit = 100

    try:
        docs = (
            db.collection("transaction")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        logs = []
        for d in docs:
            dd = d.to_dict() or {}
            dd["id"] = d.id
            logs.append(dd)
        return jsonify({"logs": logs}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@superadmin_bp.route("/inventory_overview", methods=["GET"])
@token_required
def inventory_overview():
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    store_names = user.get("store_ids", [])
    if not store_names:
        return jsonify({"rows": []}), 200

    agg = {}
    try:
        for store in store_names:
            ing_col = db.collection("stores").document(store).collection("ingredients")
            for ing_doc in ing_col.stream():
                ing = ing_doc.to_dict() or {}
                name = (ing.get("name") or "").strip()
                if not name:
                    continue

                unit = (ing.get("unit") or "").strip()
                ing_ref = ing_doc.reference

                store_total, store_earliest = _sum_available_and_earliest_exp(ing_ref, ing)

                if name not in agg:
                    agg[name] = {
                        "name": name,
                        "unit": unit,
                        "total_quantity": 0.0,
                        "earliest_expiration": None,
                        "out_of_stock_store_count": 0,
                    }

                if not agg[name]["unit"] and unit:
                    agg[name]["unit"] = unit

                agg[name]["total_quantity"] += float(store_total)

                if store_earliest:
                    cur = agg[name]["earliest_expiration"]
                    if (cur is None) or (store_earliest.isoformat() < cur):
                        agg[name]["earliest_expiration"] = store_earliest.isoformat()

                if float(store_total) <= 0:
                    agg[name]["out_of_stock_store_count"] += 1

        rows = list(agg.values())
        rows.sort(key=lambda r: r["name"])
        return jsonify({"rows": rows}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
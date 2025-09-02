# routes/superadmin.py
from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required
from datetime import datetime, timedelta, date
import calendar
import requests
from collections import defaultdict
from google.cloud import firestore


superadmin_bp = Blueprint("superadmin", __name__)


# ------------- 共用工具函式 -------------

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


# ------------- 地圖：地址轉經緯度 -------------

def geocode_address(address):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "tw",
        "accept-language": "zh-TW",
    }
    headers = {"User-Agent": "yaoyao-superadmin-map"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=8)
        data = resp.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return lat, lon
    except Exception as e:
        print(f"[geocode_address] 失敗：{address} => {e}")
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
        labels = [f"{m}月" for m in range(1, 12 + 1)]
        for store in store_names:
            month_totals = []
            for m in range(1, 13):
                start_dt, end_dt = _month_range(y, m)
                month_totals.append(_sum_store_revenue_between(store, start_dt, end_dt))
            result.append({"store_name": store, "dates": labels, "revenues": month_totals})
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


# ============ 調貨（大老闆一鍵調貨＋記錄） ============

@superadmin_bp.route("/superadmin/transfer", methods=["OPTIONS"])
def superadmin_transfer_preflight():
    return ("", 204)

@firestore.transactional
def _do_transfer(tx, *, from_ref, to_ref, transfer_ref, data, user, qty):
    from_snap = tx.get(from_ref)
    to_snap = tx.get(to_ref)

    if not from_snap.exists:
        raise ValueError("來源分店沒有此食材")

    from_qty = float((from_snap.to_dict() or {}).get("quantity", 0.0))
    if from_qty < qty:
        raise ValueError("來源庫存不足")

    # 扣來源
    tx.update(from_ref, {
        "quantity": from_qty - qty,
        "updated_at": firestore.SERVER_TIMESTAMP
    })

    # 加到目標
    if to_snap.exists:
        cur = float((to_snap.to_dict() or {}).get("quantity", 0.0))
        tx.update(to_ref, {
            "quantity": cur + qty,
            "updated_at": firestore.SERVER_TIMESTAMP
        })
    else:
        tx.set(to_ref, {
            "name": data["ingredient_name"],
            "unit": data["unit"],
            "quantity": qty,
            "updated_at": firestore.SERVER_TIMESTAMP
        })

    # 寫入調貨紀錄
    tx.set(transfer_ref, {
        "from_store": data["from_store"],
        "to_store": data["to_store"],
        "ingredient_id": data["ingredient_id"],
        "ingredient_name": data["ingredient_name"],
        "unit": data["unit"],
        "quantity": qty,
        "note": data.get("note", ""),
        "executed_by": user.get("uid"),
        "executed_at": firestore.SERVER_TIMESTAMP,
        "status": "completed"
    })


@superadmin_bp.route("/superadmin/transfer", methods=["POST"])
@token_required
def superadmin_transfer():
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    data = request.get_json() or {}
    required = ["from_store", "to_store", "ingredient_id", "ingredient_name", "unit", "quantity"]
    if any(k not in data for k in required):
        return jsonify({"error": "缺少必要欄位"}), 400

    if data["from_store"] == data["to_store"]:
        return jsonify({"error": "來源與目標分店不能相同"}), 400

    try:
        qty = float(data["quantity"])
    except Exception:
        return jsonify({"error": "quantity 必須為數字"}), 400
    if qty <= 0:
        return jsonify({"error": "quantity 必須為正數"}), 400

    from_ref = db.collection("stores").document(data["from_store"]).collection("ingredients").document(data["ingredient_id"])
    to_ref = db.collection("stores").document(data["to_store"]).collection("ingredients").document(data["ingredient_id"])
    transfer_ref = db.collection("transfers").document()

    try:
        # ✅ 直接呼叫，transactional 會自動建立 transaction
        _do_transfer(from_ref=from_ref,
                     to_ref=to_ref,
                     transfer_ref=transfer_ref,
                     data=data,
                     user=user,
                     qty=qty)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"調貨失敗：{e}"}), 500

    return jsonify({"ok": True, "transfer_id": transfer_ref.id}), 200

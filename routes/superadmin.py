from flask import Blueprint, request, jsonify 
from firebase_config import db
from routes.auth import token_required
from datetime import datetime, timedelta
import requests


superadmin_bp = Blueprint("superadmin", __name__)
# ✅ 地址轉換為經緯度的函式（OpenStreetMap Nominatim）
def geocode_address(address):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "tw",
        "accept-language": "zh-TW"
    }
    headers = {
        "User-Agent": "yaoyao-superadmin-map"
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return lat, lon
        else:
            return None, None
    except Exception as e:
        print(f"地址轉換失敗：{address} => {e}")
        return None, None

# ✅ 各分店營收折線圖 API（range: 7days / month / year）
@superadmin_bp.route("/get_all_store_revenue", methods=["GET"])
@token_required
def get_all_store_revenue():
    user = request.user
    role = user.get("role")
    store_names = user.get("store_ids", [])

    if role != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    range_type = request.args.get("range", "7days")
    today = datetime.today()
    result = []

    if range_type == "7days":
        start_date = today - timedelta(days=6)
        date_list = [(start_date + timedelta(days=i)).strftime("%m/%d") for i in range(7)]

        for store_name in store_names:
            revenues = []
            for i in range(7):
                target_date = start_date + timedelta(days=i)
                start_ts = datetime(target_date.year, target_date.month, target_date.day)
                end_ts = start_ts + timedelta(days=1)

                orders = db.collection("stores").document(store_name).collection("completed_orders") \
                    .where("completed_at", ">=", start_ts) \
                    .where("completed_at", "<", end_ts) \
                    .stream()

                total = sum(order.to_dict().get("total_price", 0) for order in orders)
                revenues.append(total)

            result.append({
                "store_name": store_name,
                "dates": date_list,
                "revenues": revenues
            })

    elif range_type == "month":
        month_str = request.args.get("month")
        if not month_str:
            return jsonify({"error": "請提供月份"}), 400

        year, month = map(int, month_str.split("-"))
        start_date = datetime(year, month, 1)
        end_day = (datetime(year, month + 1, 1) - timedelta(days=1)).day if month < 12 else 31
        today_day = today.day if (year == today.year and month == today.month) else end_day

        date_list = [f"{month:02d}/{day:02d}" for day in range(1, today_day + 1)]

        for store_name in store_names:
            revenues = []
            for day in range(1, today_day + 1):
                target_date = datetime(year, month, day)
                start_ts = datetime(target_date.year, target_date.month, target_date.day)
                end_ts = start_ts + timedelta(days=1)

                orders = db.collection("stores").document(store_name).collection("completed_orders") \
                    .where("completed_at", ">=", start_ts) \
                    .where("completed_at", "<", end_ts) \
                    .stream()

                total = sum(order.to_dict().get("total_price", 0) for order in orders)
                revenues.append(total)

            result.append({
                "store_name": store_name,
                "dates": date_list,
                "revenues": revenues
            })

    elif range_type == "year":
        year = int(request.args.get("year", today.year))
        date_list = [f"{month}月" for month in range(1, 13)]

        for store_name in store_names:
            revenues = []
            for month in range(1, 13):
                start_date = datetime(year, month, 1)
                if month == 12:
                    end_date = datetime(year + 1, 1, 1)
                else:
                    end_date = datetime(year, month + 1, 1)

                orders = db.collection("stores").document(store_name).collection("completed_orders") \
                    .where("completed_at", ">=", start_date) \
                    .where("completed_at", "<", end_date) \
                    .stream()

                total = sum(order.to_dict().get("total_price", 0) for order in orders)
                revenues.append(total)

            result.append({
                "store_name": store_name,
                "dates": date_list,
                "revenues": revenues
            })

    else:
        return jsonify({"error": "range 參數錯誤"}), 400

    return jsonify(result)

# ✅ 圓餅圖口味統計 API
@superadmin_bp.route("/get_store_flavor_sales", methods=["GET"])
@token_required
def get_store_flavor_sales():
    user = request.user
    role = user.get("role")
    store_names = user.get("store_ids", [])

    if role != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    month_str = request.args.get("month")
    if not month_str:
        return jsonify({"error": "請提供月份"}), 400

    try:
        year, month = map(int, month_str.split("-"))
        start_date = datetime(year, month, 1)
        end_date = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    except:
        return jsonify({"error": "月份格式錯誤"}), 400

    result = {}

    for store_name in store_names:
        flavor_sales = {}
        orders = db.collection("stores").document(store_name).collection("completed_orders") \
            .where("completed_at", ">=", start_date) \
            .where("completed_at", "<", end_date) \
            .stream()

        for doc in orders:
            order = doc.to_dict()
            for item in order.get("items", []):
                flavor = item.get("menu_name")
                qty = item.get("quantity", 0)
                if flavor:
                    flavor_sales[flavor] = flavor_sales.get(flavor, 0) + qty

        flavor_list = [{"name": name, "value": qty} for name, qty in flavor_sales.items()]
        result[store_name] = flavor_list

    return jsonify(result)

# ✅ 本月銷售總額與訂單總數
@superadmin_bp.route("/get_summary_this_month", methods=["GET"])
@token_required
def get_summary_this_month():
    user = request.user
    role = user.get("role")
    store_names = user.get("store_ids", [])

    if role != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    now = datetime.now()
    start_ts = datetime(now.year, now.month, 1)
    end_ts = datetime(now.year + 1, 1, 1) if now.month == 12 else datetime(now.year, now.month + 1, 1)

    total_sales = 0
    total_orders = 0

    for store_name in store_names:
        orders = db.collection("stores").document(store_name).collection("completed_orders") \
            .where("completed_at", ">=", start_ts) \
            .where("completed_at", "<", end_ts) \
            .stream()

        for order_doc in orders:
            order = order_doc.to_dict()
            total_sales += order.get("total_price", 0)
            total_orders += 1

    return jsonify({
        "total_sales": total_sales,
        "total_orders": total_orders
    })

# ✅ 銷售排行榜 API（全分店，總計 menu_name 數量）
@superadmin_bp.route("/get_top_flavors", methods=["GET"])
@token_required
def get_top_flavors():
    user = request.user
    role = user.get("role")
    store_names = user.get("store_ids", [])

    if role != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    month_str = request.args.get("month")
    if not month_str:
        return jsonify({"error": "請提供月份"}), 400

    try:
        year, month = map(int, month_str.split("-"))
        start_date = datetime(year, month, 1)
        end_date = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    except:
        return jsonify({"error": "月份格式錯誤"}), 400

    flavor_total = {}

    for store_name in store_names:
        orders = db.collection("stores").document(store_name).collection("completed_orders") \
            .where("completed_at", ">=", start_date) \
            .where("completed_at", "<", end_date) \
            .stream()

        for doc in orders:
            order = doc.to_dict()
            for item in order.get("items", []):
                flavor = item.get("menu_name")
                qty = item.get("quantity", 0)
                if flavor:
                    flavor_total[flavor] = flavor_total.get(flavor, 0) + qty

    # 排名前 10 名
    sorted_flavors = sorted(flavor_total.items(), key=lambda x: x[1], reverse=True)[:10]
    return jsonify([{"name": name, "value": qty} for name, qty in sorted_flavors])

# ✅ 查詢登入者可管理的所有分店
@superadmin_bp.route("/get_my_stores", methods=["GET"])
@token_required
def get_my_stores():
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "僅限企業主使用"}), 403

    store_ids = user.get("store_ids", [])
    return jsonify({"stores": store_ids}), 200

# ✅ 查詢指定分店的庫存清單
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
        inventory = [{"id": doc.id, **doc.to_dict()} for doc in snapshot]
        return jsonify({"store": store_name, "inventory": inventory}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@superadmin_bp.route("/get_store_revenue_rank", methods=["GET"])
@token_required
def get_store_revenue_rank():
    user = request.user
    role = user.get("role")
    store_names = user.get("store_ids", [])

    if role != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    month_str = request.args.get("month")
    if not month_str:
        return jsonify({"error": "請提供月份"}), 400

    year, month = map(int, month_str.split("-"))
    start_date = datetime(year, month, 1)
    end_date = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

    store_sales = []
    for store_name in store_names:
        orders = db.collection("stores").document(store_name).collection("completed_orders") \
            .where("completed_at", ">=", start_date) \
            .where("completed_at", "<", end_date) \
            .stream()

        total_sales = sum(order.to_dict().get("total_price", 0) for order in orders)
        store_sales.append({"store_name": store_name, "total_sales": total_sales})

    store_sales.sort(key=lambda x: x["total_sales"], reverse=True)
    return jsonify(store_sales)

# ✅ superadmin 取得所有分店的地址、經緯度與（指定期間）營收總額
@superadmin_bp.route("/get_store_locations", methods=["GET"])
@token_required
def get_store_locations():
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    # --- 期間參數解析：預設「month」 ---
    range_type = request.args.get("range", "month")
    now = datetime.now()

    if range_type == "7days":
        start_ts = datetime(now.year, now.month, now.day) - timedelta(days=6)
        end_ts = datetime(now.year, now.month, now.day) + timedelta(days=1)
    elif range_type == "month":
        month_str = request.args.get("month")
        if month_str:
            try:
                y, m = map(int, month_str.split("-"))
            except Exception:
                return jsonify({"error": "月份格式錯誤，需為 YYYY-MM"}), 400
        else:
            y, m = now.year, now.month
        start_ts = datetime(y, m, 1)
        end_ts = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
    elif range_type == "year":
        y = int(request.args.get("year", now.year))
        start_ts = datetime(y, 1, 1)
        end_ts = datetime(y + 1, 1, 1)
    else:
        return jsonify({"error": "range 參數錯誤，允許值：7days / month / year"}), 400

    # --- 掃描 users：挑出分店帳號（developer/staff） ---
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

        # --- 動態計算營收 ---
        revenue = 0
        if store_name:
            try:
                orders_ref = (
                    db.collection("stores")
                      .document(store_name)
                      .collection("completed_orders")
                      .where("completed_at", ">=", start_ts)
                      .where("completed_at", "<", end_ts)
                )
                orders = orders_ref.stream()
                revenue = sum(o.to_dict().get("total_price", 0) for o in orders)
            except Exception as e:
                print(f"[get_store_locations] 營收查詢失敗 {store_name}: {e}")

        lat, lon = geocode_address(address)

        store_list.append({
            "store_name": store_name,
            "address": address,
            "latitude": lat,
            "longitude": lon,
            "revenue": revenue
        })

    return jsonify(store_list), 200

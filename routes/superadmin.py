from flask import Blueprint, request, jsonify 
from firebase_config import db
from routes.auth import token_required
from datetime import datetime, timedelta

superadmin_bp = Blueprint("superadmin", __name__)

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
        month_str = request.args.get("month")  # 格式：2025-07
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

    month_str = request.args.get("month")  # 格式：2025-07
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

# ✅ 查詢登入者可管理的所有分店
@superadmin_bp.route("/get_my_stores", methods=["GET"])
@token_required
def get_my_stores():
    user = request.user
    if user.get("role") != "superadmin":
        return jsonify({"error": "僅限企業主使用"}), 403

    store_ids = user.get("store_ids", [])
    return jsonify({"stores": store_ids}), 200

# ✅ 查詢指定分店的庫存清單（superadmin 專用）
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

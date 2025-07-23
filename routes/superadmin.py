from flask import Blueprint, request, jsonify 
from firebase_config import db
from routes.auth import token_required
from datetime import datetime, timedelta

superadmin_bp = Blueprint("superadmin", __name__)

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

# ✅ 新增 API：取得各分店每種口味的總銷量（圓餅圖用）
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
        if month == 12:
            end_date = datetime(year + 1, 1, 1)
        else:
            end_date = datetime(year, month + 1, 1)
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
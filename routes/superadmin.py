from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required  # ✅ 使用自訂 token 驗證

superadmin_bp = Blueprint("superadmin", __name__)

# ✅ 營收統計：每家店的 completed_orders 訂單數與總營收
@superadmin_bp.route("/superadmin/sales_summary", methods=["GET"])
@token_required
def sales_summary():
    user = request.user  # 從 token_required 中設的 request.user
    role = user.get("role")
    store_names = user.get("store_ids", [])  # superadmin 才有 store_ids 欄位

    if role != "superadmin":
        return jsonify({"error": "你不是企業主"}), 403

    result = []

    for store_name in store_names:
        try:
            orders_ref = db.collection("stores").document(store_name).collection("completed_orders").stream()
            total_revenue = 0
            order_count = 0

            for doc in orders_ref:
                order = doc.to_dict()
                order_count += 1
                total_revenue += order.get("total_price", 0)  # 若無 total_price 則加 0

            result.append({
                "store_name": store_name,
                "order_count": order_count,
                "total_revenue": total_revenue
            })

        except Exception as e:
            result.append({
                "store_name": store_name,
                "error": f"資料讀取失敗：{str(e)}"
            })

    return jsonify(result)

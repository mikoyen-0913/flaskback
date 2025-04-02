import datetime
from flask import Blueprint, request, jsonify
from firebase_admin import firestore
from firebase_config import db

orders_bp = Blueprint('orders', __name__)
menus_collection = "menus"
orders_collection = "orders"

# ✅ 建立訂單（單品項或多品項皆支援）
@orders_bp.route('/place_order', methods=['POST'])
def place_order():
    try:
        data = request.get_json()

        # 如果是單品項就轉成 items 陣列格式
        if "menu_id" in data and "quantity" in data:
            data = {
                "items": [
                    {
                        "menu_id": data["menu_id"],
                        "quantity": data["quantity"]
                    }
                ]
            }

        items = data.get("items")
        if not isinstance(items, list) or not items:
            return jsonify({"error": "items 欄位需為陣列且不可為空"}), 400

        order_items = []
        total_price = 0

        for item in items:
            menu_id = item.get("menu_id")
            quantity = item.get("quantity")
            if not menu_id or not isinstance(quantity, (int, float)):
                return jsonify({"error": "每項需包含 menu_id 和 quantity"}), 400

            menu_doc = db.collection(menus_collection).document(menu_id).get()
            if not menu_doc.exists:
                return jsonify({"error": f"找不到菜單 {menu_id}"}), 404

            menu_data = menu_doc.to_dict()
            unit_price = menu_data["price"]
            subtotal = unit_price * quantity

            order_items.append({
                "menu_id": menu_id,
                "menu_name": menu_data["name"],
                "unit_price": unit_price,
                "quantity": quantity,
                "subtotal": subtotal
            })
            total_price += subtotal

        order_data = {
            "items": order_items,
            "total_price": total_price,
            "timestamp": datetime.datetime.utcnow()
        }

        doc_ref = db.collection(orders_collection).add(order_data)
        return jsonify({
            "message": "訂單建立成功",
            "order_id": doc_ref[1].id,
            "order": order_data
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 查詢所有訂單（統一格式）
@orders_bp.route('/get_orders', methods=['GET'])
def get_orders():
    try:
        orders_ref = db.collection(orders_collection).order_by("timestamp", direction=firestore.Query.DESCENDING).stream()
        orders = []
        for doc in orders_ref:
            data = doc.to_dict()
            # 確保所有訂單都有 items 陣列
            if "items" not in data or not isinstance(data["items"], list):
                if all(k in data for k in ("menu_id", "menu_name", "quantity", "unit_price", "total_price")):
                    data["items"] = [
                        {
                            "menu_id": data["menu_id"],
                            "menu_name": data["menu_name"],
                            "quantity": data["quantity"],
                            "unit_price": data["unit_price"],
                            "subtotal": data["total_price"]
                        }
                    ]
                else:
                    continue
            data["id"] = doc.id
            orders.append(data)

        return jsonify({"orders": orders}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 刪除單筆訂單
@orders_bp.route('/delete_order/<order_id>', methods=['DELETE'])
def delete_order(order_id):
    try:
        db.collection(orders_collection).document(order_id).delete()
        return jsonify({"message": "訂單刪除成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 批次刪除訂單
@orders_bp.route('/delete_multiple_orders', methods=['POST'])
def delete_multiple_orders():
    try:
        data = request.get_json()
        ids = data.get("ids", [])
        if not isinstance(ids, list):
            return jsonify({"error": "ids 應為陣列"}), 400

        for order_id in ids:
            db.collection(orders_collection).document(order_id).delete()

        return jsonify({"message": f"已刪除 {len(ids)} 筆訂單"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 清理不符合 items 格式的舊訂單資料
@orders_bp.route('/clean_invalid_orders', methods=['POST'])
def clean_invalid_orders():
    try:
        orders_ref = db.collection(orders_collection).stream()
        deleted = 0
        for doc in orders_ref:
            data = doc.to_dict()
            if "items" not in data or not isinstance(data["items"], list):
                db.collection(orders_collection).document(doc.id).delete()
                deleted += 1

        return jsonify({"message": f"已清理 {deleted} 筆不合法訂單資料"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
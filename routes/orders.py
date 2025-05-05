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
            return jsonify({"error": "items 欄位須為陣列且不可為空"}), 400

        order_items = []
        total_price = 0

        for item in items:
            menu_id = item.get("menu_id")
            quantity = item.get("quantity")
            if not menu_id or not isinstance(quantity, (int, float)):
                return jsonify({"error": "每項須包含 menu_id 和 quantity"}), 400

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
            "timestamp": datetime.datetime.utcnow(),
            "status": "pending"  # 預設為未完成
        }

        doc_ref = db.collection(orders_collection).add(order_data)
        return jsonify({
            "message": "訂單建立成功",
            "order_id": doc_ref[1].id,
            "order": order_data
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 查詢所有訂單
@orders_bp.route('/get_orders', methods=['GET'])
def get_orders():
    try:
        orders_ref = db.collection(orders_collection).order_by("timestamp", direction=firestore.Query.DESCENDING).stream()
        orders = []
        for doc in orders_ref:
            data = doc.to_dict()
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

# ✅ 更新訂單
@orders_bp.route('/update_order/<order_id>', methods=['PUT'])
def update_order(order_id):
    try:
        data = request.get_json()
        items = data.get("items")

        if not isinstance(items, list) or not items:
            return jsonify({"error": "items 資料格式錯誤"}), 400

        order_items = []
        total_price = 0

        for item in items:
            menu_name = item.get("menu_name")
            quantity = item.get("quantity")

            if not menu_name or not isinstance(quantity, (int, float)):
                return jsonify({"error": "每項須包含 menu_name 和 quantity"}), 400

            menus_ref = db.collection(menus_collection).where("name", "==", menu_name).stream()
            menu_doc = next(menus_ref, None)

            if not menu_doc:
                return jsonify({"error": f"找不到菜單項目: {menu_name}"}), 404

            menu_data = menu_doc.to_dict()
            unit_price = menu_data["price"]
            subtotal = unit_price * quantity

            order_items.append({
                "menu_id": menu_doc.id,
                "menu_name": menu_name,
                "unit_price": unit_price,
                "quantity": quantity,
                "subtotal": subtotal
            })

            total_price += subtotal

        db.collection(orders_collection).document(order_id).update({
            "items": order_items,
            "total_price": total_price,
            "timestamp": datetime.datetime.utcnow()
        })

        return jsonify({"message": "訂單更新成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 完成單筆訂單
@orders_bp.route('/complete_order/<order_id>', methods=['POST'])
def complete_order(order_id):
    try:
        db.collection(orders_collection).document(order_id).update({
            "status": "done"
        })
        return jsonify({"message": "訂單已正常記錄為 done"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 批次完成多筆訂單
@orders_bp.route('/complete_multiple_orders', methods=['POST'])
def complete_multiple_orders():
    try:
        data = request.get_json()
        ids = data.get("ids", [])
        if not isinstance(ids, list):
            return jsonify({"error": "ids 應為陣列"}), 400

        for order_id in ids:
            db.collection(orders_collection).document(order_id).update({
                "status": "done"
            })

        return jsonify({"message": f"已完成 {len(ids)} 筆訂單"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 復原所有完成的訂單
@orders_bp.route('/revert_all_completed_orders', methods=['POST'])
def revert_all_completed_orders():
    try:
        orders_ref = db.collection(orders_collection).where("status", "==", "done").stream()
        for doc in orders_ref:
            db.collection(orders_collection).document(doc.id).update({
                "status": "pending"
            })

        return jsonify({"message": "所有 done 訂單已復原為 pending"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

        # ✅ 新增：將訂單移至 completed_orders 並從 orders 刪除
@orders_bp.route('/move_to_completed/<order_id>', methods=['POST'])
def move_to_completed(order_id):
    try:
        order_ref = db.collection(orders_collection).document(order_id)
        order_data = order_ref.get().to_dict()

        if not order_data:
            return jsonify({"error": "訂單不存在"}), 404

        # 寫入 completed_orders 並刪除原始
        db.collection("completed_orders").document(order_id).set(order_data)
        order_ref.delete()

        return jsonify({"message": "訂單已移動至 completed_orders"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ 新增：查詢所有 completed_orders 資料
@orders_bp.route('/get_completed_orders', methods=['GET'])
def get_completed_orders():
    try:
        docs = db.collection("completed_orders").order_by("timestamp", direction=firestore.Query.DESCENDING).stream()
        orders = []
        for doc in docs:
            order = doc.to_dict()
            order['id'] = doc.id
            orders.append(order)
        return jsonify({"orders": orders}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

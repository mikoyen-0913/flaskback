import datetime
from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required
from google.cloud import firestore
from google.cloud.firestore import Increment

orders_bp = Blueprint('orders', __name__)
menus_collection = "menus"
orders_collection = "orders"

# ✅ 建立訂單
@orders_bp.route('/place_order', methods=['POST'])
@token_required
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
            return jsonify({"error": "items 欄位必須為陣列且不可為空"}), 400

        order_items = []
        total_price = 0

        for item in items:
            menu_id = item.get("menu_id")
            quantity = item.get("quantity")
            if not menu_id or not isinstance(quantity, (int, float)):
                return jsonify({"error": "每個項目必須含有 menu_id 和 quantity"}), 400

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

        now = datetime.datetime.utcnow()
        order_data = {
            "items": order_items,
            "total_price": total_price,
            "created_at": now,
            "timestamp": now,
            "status": "pending"
        }

        doc_ref = db.collection(orders_collection).add(order_data)
        return jsonify({
            "message": "訂單成立成功",
            "order_id": doc_ref[1].id,
            "order": order_data
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ 查詢所有訂單
@orders_bp.route('/get_orders', methods=['GET'])
@token_required
def get_orders():
    try:
        orders_ref = db.collection(orders_collection).order_by("created_at").stream()
        orders = []
        for doc in orders_ref:
            data = doc.to_dict()
            if "items" not in data or not isinstance(data["items"], list):
                continue
            data["id"] = doc.id
            orders.append(data)

        return jsonify({"orders": orders}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 刪除單筆訂單
@orders_bp.route('/delete_order/<order_id>', methods=['DELETE'])
@token_required
def delete_order(order_id):
    try:
        db.collection(orders_collection).document(order_id).delete()
        return jsonify({"message": "訂單刪除成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 更新訂單
@orders_bp.route('/update_order/<order_id>', methods=['PUT'])
@token_required
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
                return jsonify({"error": "每項必含 menu_name 和 quantity"}), 400

            menus_ref = db.collection(menus_collection).where("name", "==", menu_name).stream()
            menu_doc = next(menus_ref, None)
            if not menu_doc:
                return jsonify({"error": f"找不到菜單: {menu_name}"}), 404

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

# ✅ 完成訂單並扣除庫存
@orders_bp.route("/complete_order/<order_id>", methods=["POST"])
@token_required
def complete_order(order_id):
    try:
        UNIT_ALIAS = {
            "g": "克",
            "kg": "克",
            "ml": "毫升",
            "l": "毫升",
            "公克": "克",
            "公升": "毫升"
        }

        MULTIPLIER = {
            ("kg", "克"): 1000,
            ("l", "毫升"): 1000,
            # 其他單位預設為 1
        }

        def normalize_unit(unit):
            return UNIT_ALIAS.get(unit.strip().lower(), unit.strip())

        def convert_amount(ingredient_unit, recipe_unit, amount):
            key = (ingredient_unit, recipe_unit)
            if key in MULTIPLIER:
                return amount / MULTIPLIER[key]
            elif (recipe_unit, ingredient_unit) in MULTIPLIER:
                return amount * MULTIPLIER[(recipe_unit, ingredient_unit)]
            return amount  # 預設直接扣

        order_ref = db.collection(orders_collection).document(order_id)
        order_doc = order_ref.get()
        if not order_doc.exists:
            return jsonify({"error": "訂單不存在"}), 404

        order_data = order_doc.to_dict()
        items = order_data.get("items", [])

        for item in items:
            menu_name = item.get("menu_name")
            quantity = item.get("quantity", 1)

            recipe_doc = db.collection("recipes").document(menu_name).get()
            if not recipe_doc.exists:
                continue
            recipe = recipe_doc.to_dict()

            for ing_name, detail in recipe.items():
                amount = detail.get("amount")
                recipe_unit = normalize_unit(detail.get("unit"))

                ing_query = db.collection("ingredients").where("name", "==", ing_name).limit(1).stream()
                for ing_doc in ing_query:
                    ing_data = ing_doc.to_dict()
                    ingredient_unit = normalize_unit(ing_data.get("unit"))

                    if recipe_unit != ingredient_unit:
                        # 嘗試轉換數量
                        try:
                            adjusted_amount = convert_amount(ingredient_unit, recipe_unit, amount)
                        except:
                            return jsonify({"error": f"{ing_name} 單位不符且無法轉換"}), 400
                    else:
                        adjusted_amount = amount

                    db.collection("ingredients").document(ing_doc.id).update({
                        "quantity": Increment(-adjusted_amount * quantity)
                    })

        order_data["completed_at"] = firestore.SERVER_TIMESTAMP
        db.collection("completed_orders").document(order_id).set(order_data)
        order_ref.delete()

        return jsonify({"message": "訂單已完成並已扣庫存"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ 查詢已完成訂單
@orders_bp.route('/get_completed_orders', methods=['GET'])
@token_required
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

# ✅ 公開版訂單下單（不需登入）
@orders_bp.route('/public_place_order', methods=['POST'])
def public_place_order():
    try:
        data = request.get_json()
        items = data.get("items")
        if not isinstance(items, list) or not items:
            return jsonify({"error": "items 欄位必須為陣列且不可為空"}), 400

        order_items = []
        total_price = 0

        for item in items:
            menu_id = item.get("menu_id")
            quantity = item.get("quantity")
            if not menu_id or not isinstance(quantity, (int, float)):
                return jsonify({"error": "每個項目必須含有 menu_id 和 quantity"}), 400

            menu_doc = db.collection("menus").document(menu_id).get()
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

        now = datetime.datetime.utcnow()
        order_data = {
            "items": order_items,
            "total_price": total_price,
            "created_at": now,
            "timestamp": now,
            "status": "pending"
        }

        doc_ref = db.collection("orders").add(order_data)
        return jsonify({
            "message": "訂單成立成功",
            "order_id": doc_ref[1].id,
            "order": order_data
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

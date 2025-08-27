import datetime
from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required
from google.cloud import firestore
from google.cloud.firestore import Increment

orders_bp = Blueprint('orders', __name__)

# === 小工具：取得台灣時區的今天字串 YYYYMMDD ===
def taipei_today_str_from_utc(dt_utc: datetime.datetime | None = None) -> str:
    if dt_utc is None:
        dt_utc = datetime.datetime.utcnow()
    # 台灣時區 +08:00（避免 UTC 跨日造成歸檔錯誤）
    return (dt_utc + datetime.timedelta(hours=8)).strftime("%Y%m%d")


# =========================
# 下單（需要登入）
# =========================
@orders_bp.route('/place_order', methods=['POST'])
@token_required
def place_order():
    try:
        store_name = request.user.get("store_name")
        print("🔥 訂單送到 store:", store_name)

        data = request.get_json()

        # 若是單品項格式，自動轉換成陣列
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

            # 🔁 改為全域共用菜單
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
        date_str = now.strftime("%Y%m%d")
        counter_doc_ref = db.collection("stores").document(store_name).collection("daily_counter").document(date_str)

        # 🔢 產生 order_number（與 public_place_order 相同）
        transaction = db.transaction()

        @firestore.transactional
        def increment_order_number(transaction):
            snapshot = counter_doc_ref.get(transaction=transaction)
            current = snapshot.to_dict().get("count", 0) if snapshot.exists else 0
            next_number = current + 1
            transaction.set(counter_doc_ref, {"count": next_number})
            return next_number

        order_number = increment_order_number(transaction)

        order_data = {
            "order_number": order_number,
            "items": order_items,
            "total_price": total_price,
            "created_at": now,
            "timestamp": now,
            "status": "pending",
            "store_name": store_name,  # 供後續 collectionGroup 查詢
        }

        # ✅ 寫入 store 對應的 orders 子集合
        doc_ref = db.collection("stores").document(store_name).collection("orders").add(order_data)
        print("✅ 建立訂單 ID:", doc_ref[1].id)

        return jsonify({
            "message": "訂單成立成功",
            "order_id": doc_ref[1].id,
            "order_number": order_number,
            "order": order_data
        }), 200

    except Exception as e:
        print("❌ 錯誤：", str(e))
        return jsonify({"error": str(e)}), 500


# =========================
# 查詢 pending 訂單
# =========================
@orders_bp.route('/get_orders', methods=['GET'])
@token_required
def get_orders():
    try:
        store_name = request.user.get("store_name")
        orders_ref = (db.collection("stores").document(store_name)
                        .collection("orders")
                        .order_by("created_at")
                        .stream())
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


# =========================
# 刪除 pending 訂單
# =========================
@orders_bp.route('/delete_order/<order_id>', methods=['DELETE'])
@token_required
def delete_order(order_id):
    try:
        store_name = request.user.get("store_name")
        db.collection("stores").document(store_name).collection("orders").document(order_id).delete()
        return jsonify({"message": "訂單刪除成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# 更新 pending 訂單
# =========================
@orders_bp.route('/update_order/<order_id>', methods=['PUT'])
@token_required
def update_order(order_id):
    try:
        store_name = request.user.get("store_name")
        data = request.get_json()
        items = data.get("items")
        if not isinstance(items, list) or not items:
            return jsonify({"error": "items 資料格式錯誤"}), 400

        order_items = []
        total_price = 0

        for item in items:
            menu_id = item.get("menu_id")
            quantity = item.get("quantity")

            if not menu_id or not isinstance(quantity, (int, float)):
                return jsonify({"error": "每項必含 menu_id 和 quantity"}), 400

            menu_doc = db.collection("menus").document(menu_id).get()
            if not menu_doc.exists:
                return jsonify({"error": f"找不到菜單 ID: {menu_id}"}), 404

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

        db.collection("stores").document(store_name).collection("orders").document(order_id).update({
            "items": order_items,
            "total_price": total_price,
            "timestamp": datetime.datetime.utcnow()
        })

        return jsonify({"message": "訂單更新成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# 完成單筆訂單並扣庫存
# =========================
@orders_bp.route("/complete_order/<order_id>", methods=["POST"])
@token_required
def complete_order(order_id):
    try:
        store_name = request.user.get("store_name")

        UNIT_ALIAS = {
            "g": "克", "kg": "克", "ml": "毫升", "l": "毫升", "公克": "克", "公升": "毫升"
        }

        MULTIPLIER = {
            ("kg", "克"): 1000,
            ("l", "毫升"): 1000,
        }

        def normalize_unit(unit):
            return UNIT_ALIAS.get(unit.strip().lower(), unit.strip())

        def convert_amount(ingredient_unit, recipe_unit, amount):
            key = (ingredient_unit, recipe_unit)
            if key in MULTIPLIER:
                return amount / MULTIPLIER[key]
            elif (recipe_unit, ingredient_unit) in MULTIPLIER:
                return amount * MULTIPLIER[(recipe_unit, ingredient_unit)]
            return amount

        order_ref = db.collection("stores").document(store_name).collection("orders").document(order_id)
        order_doc = order_ref.get()
        if not order_doc.exists:
            return jsonify({"error": "訂單不存在"}), 404

        order_data = order_doc.to_dict()
        items = order_data.get("items", [])

        for item in items:
            menu_name = item.get("menu_name")
            quantity = item.get("quantity", 1)

            recipe_doc = (db.collection("stores").document(store_name)
                            .collection("recipes").document(menu_name).get())
            if not recipe_doc.exists:
                continue
            recipe = recipe_doc.to_dict()

            for ing_name, detail in recipe.items():
                amount = detail.get("amount")
                recipe_unit = normalize_unit(detail.get("unit"))

                ing_query = (db.collection("stores").document(store_name)
                               .collection("ingredients")
                               .where("name", "==", ing_name).limit(1).stream())
                for ing_doc in ing_query:
                    ing_data = ing_doc.to_dict()
                    ingredient_unit = normalize_unit(ing_data.get("unit"))

                    if recipe_unit != ingredient_unit:
                        try:
                            adjusted_amount = convert_amount(ingredient_unit, recipe_unit, amount)
                        except:
                            return jsonify({"error": f"{ing_name} 單位不符且無法轉換"}), 400
                    else:
                        adjusted_amount = amount

                    db.collection("stores").document(store_name).collection("ingredients").document(ing_doc.id).update({
                        "quantity": Increment(-adjusted_amount * quantity)
                    })

        # === 完成資訊 & 新結構寫入 ===
        now_utc = datetime.datetime.utcnow()
        ymd = taipei_today_str_from_utc(now_utc)
        order_number = order_data.get("order_number", 0)
        doc_id = f"{ymd}-{order_number}"

        order_data["status"] = "completed"
        order_data["used_in_inventory_refresh"] = False
        order_data["completed_at"] = firestore.SERVER_TIMESTAMP
        order_data["timestamp"] = firestore.SERVER_TIMESTAMP
        order_data["store_name"] = store_name  # 供 collectionGroup 篩選

        dates_ref = (db.collection("stores").document(store_name)
                        .collection("dates").document(ymd)
                        .collection("completed_orders").document(doc_id))
        dates_ref.set(order_data)

        # 刪掉 pending
        order_ref.delete()

        return jsonify({"message": "訂單已完成並已扣庫存"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# 批次完成多筆並扣庫存
# =========================
@orders_bp.route("/complete_multiple_orders", methods=["POST"])
@token_required
def complete_multiple_orders():
    try:
        store_name = request.user.get("store_name")
        data = request.get_json()
        ids = data.get("ids", [])

        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "請提供要完成的訂單 ID 陣列"}), 400

        UNIT_ALIAS = {
            "g": "克", "kg": "克", "ml": "毫升", "l": "毫升", "公克": "克", "公升": "毫升"
        }

        MULTIPLIER = {
            ("kg", "克"): 1000,
            ("l", "毫升"): 1000,
        }

        def normalize_unit(unit):
            return UNIT_ALIAS.get(unit.strip().lower(), unit.strip())

        def convert_amount(ingredient_unit, recipe_unit, amount):
            key = (ingredient_unit, recipe_unit)
            if key in MULTIPLIER:
                return amount / MULTIPLIER[key]
            elif (recipe_unit, ingredient_unit) in MULTIPLIER:
                return amount * MULTIPLIER[(recipe_unit, ingredient_unit)]
            return amount

        for order_id in ids:
            order_ref = db.collection("stores").document(store_name).collection("orders").document(order_id)
            order_doc = order_ref.get()
            if not order_doc.exists:
                continue

            order_data = order_doc.to_dict()
            items = order_data.get("items", [])

            for item in items:
                menu_name = item.get("menu_name")
                quantity = item.get("quantity", 1)

                recipe_doc = (db.collection("stores").document(store_name)
                                .collection("recipes").document(menu_name).get())
                if not recipe_doc.exists:
                    continue
                recipe = recipe_doc.to_dict()

                for ing_name, detail in recipe.items():
                    amount = detail.get("amount")
                    recipe_unit = normalize_unit(detail.get("unit"))

                    ing_query = (db.collection("stores").document(store_name)
                                   .collection("ingredients")
                                   .where("name", "==", ing_name).limit(1).stream())
                    for ing_doc in ing_query:
                        ing_data = ing_doc.to_dict()
                        ingredient_unit = normalize_unit(ing_data.get("unit"))

                        if recipe_unit != ingredient_unit:
                            try:
                                adjusted_amount = convert_amount(ingredient_unit, recipe_unit, amount)
                            except:
                                continue
                        else:
                            adjusted_amount = amount

                        db.collection("stores").document(store_name).collection("ingredients").document(ing_doc.id).update({
                            "quantity": Increment(-adjusted_amount * quantity)
                        })

            # === 完成資訊 & 新結構寫入 ===
            now_utc = datetime.datetime.utcnow()
            ymd = taipei_today_str_from_utc(now_utc)
            order_number = order_data.get("order_number", 0)
            doc_id = f"{ymd}-{order_number}"

            order_data["status"] = "completed"
            order_data["used_in_inventory_refresh"] = False
            order_data["completed_at"] = firestore.SERVER_TIMESTAMP
            order_data["timestamp"] = firestore.SERVER_TIMESTAMP
            order_data["store_name"] = store_name  # 供 collectionGroup 篩選

            dates_ref = (db.collection("stores").document(store_name)
                            .collection("dates").document(ymd)
                            .collection("completed_orders").document(doc_id))
            dates_ref.set(order_data)

            # 刪掉 pending
            order_ref.delete()

        return jsonify({"message": "多筆訂單完成成功"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# 查詢已完成訂單（新版：依日期）
# /get_completed_orders?date=YYYYMMDD
# 若未提供 date，預設為今天（台灣時間）
# =========================
@orders_bp.route('/get_completed_orders', methods=['GET'])
@token_required
def get_completed_orders():
    try:
        store_name = request.user.get("store_name")
        date_str = request.args.get("date")
        if not date_str:
            date_str = taipei_today_str_from_utc()

        docs = (db.collection("stores").document(store_name)
                    .collection("dates").document(date_str)
                    .collection("completed_orders")
                    .order_by("timestamp", direction=firestore.Query.DESCENDING)
                    .stream())
        orders = []
        for doc in docs:
            order = doc.to_dict()
            order['id'] = doc.id  # 例如 20250827-46
            orders.append(order)
        return jsonify({"date": date_str, "orders": orders}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# 公開版下單（不需登入）
# =========================
@orders_bp.route('/public_place_order', methods=['POST'])
def public_place_order():
    try:
        data = request.get_json()
        store_name = data.get("store_name")
        items = data.get("items")

        if not store_name:
            return jsonify({"error": "缺少 store_name"}), 400
        if not isinstance(items, list) or not items:
            return jsonify({"error": "items 欄位必須為陣列且不可為空"}), 400

        now = datetime.datetime.utcnow()
        date_str = now.strftime("%Y%m%d")
        counter_doc_ref = db.collection("stores").document(store_name).collection("daily_counter").document(date_str)

        # ✅ 建立 Transaction 物件
        transaction = db.transaction()

        # ✅ 宣告交易函式
        @firestore.transactional
        def increment_order_number(transaction):
            snapshot = counter_doc_ref.get(transaction=transaction)
            current = snapshot.to_dict().get("count", 0) if snapshot.exists else 0
            next_number = current + 1
            transaction.set(counter_doc_ref, {"count": next_number})
            return next_number

        # ✅ 執行 Transaction
        order_number = increment_order_number(transaction)

        # 🔄 建立訂單內容
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
            total_price += subtotal

            order_items.append({
                "menu_id": menu_id,
                "menu_name": menu_data["name"],
                "unit_price": unit_price,
                "quantity": quantity,
                "subtotal": subtotal
            })

        order_data = {
            "order_number": order_number,
            "items": order_items,
            "total_price": total_price,
            "created_at": now,
            "timestamp": now,
            "status": "pending",
            "store_name": store_name,
        }

        doc_ref = db.collection("stores").document(store_name).collection("orders").add(order_data)

        return jsonify({
            "message": "訂單成立成功",
            "order_id": doc_ref[1].id,
            "order_number": order_number,
            "order": order_data
        }), 200

    except Exception as e:
        print("❌ 錯誤：", str(e))
        return jsonify({"error": str(e)}), 500


# =========================
# 營收統計（相容新結構）
# 透過 collection group 查全部 completed_orders，
# 以 store_name + timestamp 篩選最近 N 天
# =========================
@orders_bp.route("/get_sales_summary", methods=["GET"])
@token_required
def get_sales_summary():
    try:
        store_name = request.user.get("store_name")
        days = int(request.args.get("days", 7))  # 可指定 7、14、30
        now = datetime.datetime.utcnow()
        start_date = now - datetime.timedelta(days=days)

        # 使用 collectionGroup 搜尋所有 dates/*/completed_orders
        docs = (db.collection_group("completed_orders")
                  .where("store_name", "==", store_name)
                  .where("timestamp", ">=", start_date)
                  .stream())

        sales_by_date: dict[str, float] = {}

        for doc in docs:
            data = doc.to_dict()
            ts = data.get("timestamp")
            if not ts:
                continue
            date_key = ts.strftime("%Y-%m-%d")
            sales_by_date[date_key] = sales_by_date.get(date_key, 0) + data.get("total_price", 0)

        # 依日期排序
        sorted_data = sorted(sales_by_date.items())
        result = [{"date": k, "total": v} for k, v in sorted_data]

        return jsonify({"summary": result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

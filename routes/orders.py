# routes/orders.py
from datetime import datetime, timedelta, date  # ← 解法1：這行統一
from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required
from google.cloud import firestore
from google.cloud.firestore import Increment

orders_bp = Blueprint('orders', __name__)

# === 小工具：取得台灣時區的今天字串 YYYYMMDD ===
def taipei_today_str_from_utc(dt_utc: datetime | None = None) -> str:
    if dt_utc is None:
        dt_utc = datetime.utcnow()
    # 台灣時區 +08:00（避免 UTC 跨日造成歸檔錯誤）
    return (dt_utc + timedelta(hours=8)).strftime("%Y%m%d")


# =========================
# 下單（需要登入）
# =========================
@orders_bp.route('/place_order', methods=['POST'])
@token_required
def place_order():
    try:
        store_name = request.user.get("store_name")
        print("訂單送到 store:", store_name)

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

            # 改為全域共用菜單
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

        now = datetime.utcnow()
        date_str = now.strftime("%Y%m%d")
        counter_doc_ref = db.collection("stores").document(store_name).collection("daily_counter").document(date_str)

        # 產生 order_number（與 public_place_order 相同）
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

        # 寫入 store 對應的 orders 子集合
        doc_ref = db.collection("stores").document(store_name).collection("orders").add(order_data)
        print("建立訂單 ID:", doc_ref[1].id)

        return jsonify({
            "message": "訂單成立成功",
            "order_id": doc_ref[1].id,
            "order_number": order_number,
            "order": order_data
        }), 200

    except Exception as e:
        print("錯誤：", str(e))
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
            "timestamp": datetime.utcnow()
        })

        return jsonify({"message": "訂單更新成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------
# Running Total：把完成訂單即時累加到 daily_summary/summary
# ----------------------------------------------------
def _apply_order_to_running_total(store_name: str, ymd: str, completed_doc_id: str, order_data: dict):
    """
    將一筆 completed order 累加到 `stores/{store}/dates/{ymd}/daily_summary/summary`
    - 使用 Transaction + Increment 原子更新
    - 以 daily_summary_applied/{completed_doc_id} 作為防重旗標（冪等）
    欄位：revenue, orders_count, items_count, flavor_counts.<mid>, flavor_revenue.<mid>, flavor_labels.<mid>
    """
    summary_ref = (db.collection("stores").document(store_name)
                     .collection("dates").document(ymd)
                     .collection("daily_summary").document("summary"))
    applied_flag_ref = (db.collection("stores").document(store_name)
                          .collection("dates").document(ymd)
                          .collection("daily_summary_applied").document(completed_doc_id))

    items = order_data.get("items", []) or []

    # 計算本單合計
    total_qty = 0
    total_price = 0
    flavor_increments = []  # (mid, mname, qty, sub)

    # 允許使用 order 層級 total_price；沒有就由 items 小計
    if isinstance(order_data.get("total_price"), (int, float)):
        total_price = int(order_data["total_price"])

    for it in items:
        mid = str(it.get("menu_id", "")).strip()
        mname = it.get("menu_name") or mid
        qty = 0
        sub = 0
        try:
            qty = int(it.get("quantity", 0))
        except Exception:
            pass
        try:
            sub = int(it.get("subtotal", it.get("total", 0) or 0))
        except Exception:
            try:
                sub = int(float(it.get("subtotal", it.get("total", 0) or 0)))
            except Exception:
                sub = 0

        total_qty += max(qty, 0)
        if not isinstance(order_data.get("total_price"), (int, float)):
            total_price += max(sub, 0)

        if mid:
            flavor_increments.append((mid, mname, max(qty, 0), max(sub, 0)))

    @firestore.transactional
    def _txn(transaction: firestore.Transaction):
        # 防重：已套用則跳過
        applied_snap = applied_flag_ref.get(transaction=transaction)
        if applied_snap.exists:
            return "already_applied"

        # 確保 summary 基礎欄位存在
        base = {
            "store": store_name,
            "date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
            "monthKey": ymd[:6],
        }
        transaction.set(summary_ref, base, merge=True)

        # 準備更新
        updates = {
            "revenue": Increment(int(total_price)),
            "orders_count": Increment(1),
            "items_count": Increment(int(total_qty)),
            "last_updated_at": firestore.SERVER_TIMESTAMP,
        }
        for mid, mname, qty, sub in flavor_increments:
            updates[f"flavor_counts.{mid}"] = Increment(int(qty))
            updates[f"flavor_revenue.{mid}"] = Increment(int(sub))
            updates[f"flavor_labels.{mid}"] = mname  # 覆寫同值冪等

        transaction.update(summary_ref, updates)

        # 打防重旗標
        transaction.set(applied_flag_ref, {
            "order_id": completed_doc_id,
            "applied_at": firestore.SERVER_TIMESTAMP,
        }, merge=False)

        return "applied"

    tx = db.transaction()
    return _txn(tx)


# =========================
# 完成單筆訂單並扣庫存（含 running total）
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

        # ---- 扣庫存 ----
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
        now_utc = datetime.utcnow()
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

        # ---- Running total：即時累加到當天 summary（冪等、防重）----
        _apply_order_to_running_total(store_name, ymd, doc_id, order_data)

        # 刪掉 pending
        order_ref.delete()

        return jsonify({"message": "訂單已完成並已扣庫存"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# 批次完成多筆並扣庫存（含 running total）
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

            # ---- 扣庫存 ----
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
            now_utc = datetime.utcnow()
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

            # ---- Running total：即時累加到當天 summary（冪等、防重）----
            _apply_order_to_running_total(store_name, ymd, doc_id, order_data)

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

        now = datetime.utcnow()
        date_str = now.strftime("%Y%m%d")
        counter_doc_ref = db.collection("stores").document(store_name).collection("daily_counter").document(date_str)

        transaction = db.transaction()

        @firestore.transactional
        def increment_order_number(transaction):
            snapshot = counter_doc_ref.get(transaction=transaction)
            current = snapshot.to_dict().get("count", 0) if snapshot.exists else 0
            next_number = current + 1
            transaction.set(counter_doc_ref, {"count": next_number})
            return next_number

        order_number = increment_order_number(transaction)

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
        print("錯誤：", str(e))
        return jsonify({"error": str(e)}), 500


# =========================
# 營收統計（相容新 daily_summary 結構）
# 透過 stores/{store}/dates/{YYYYMMDD}/daily_summary/summary
# 讀取最近 N 天（7/14/30）每天的 revenue
# 回傳：[{date: 'YYYY-MM-DD', total: <int>}...]
# =========================
@orders_bp.route("/get_sales_summary", methods=["GET"])
@token_required
def get_sales_summary():
    try:
        store_name = request.user.get("store_name")
        if not store_name:
            return jsonify({"error": "找不到 store_name（請確認 token 或使用者欄位）"}), 400

        days_raw = request.args.get("days", "7")
        try:
            days = int(str(days_raw).strip())
        except Exception:
            return jsonify({"error": f"days 需為整數（7/14/30），收到：{days_raw}"}), 400
        if days not in (7, 14, 30):
            return jsonify({"error": f"允許的 days：7、14、30，收到：{days}"}), 400

        today = date.today()
        start_dt = today - timedelta(days=days - 1)

        results = []
        for i in range(days):
            d = start_dt + timedelta(days=i)
            ymd = f"{d.year}{d.month:02d}{d.day:02d}"
            doc_ref = (
                db.collection("stores").document(store_name)
                  .collection("dates").document(ymd)
                  .collection("daily_summary").document("summary")
            )
            snap = doc_ref.get()
            revenue = 0
            if snap.exists:
                data = snap.to_dict() or {}
                try:
                    revenue = int(data.get("revenue", 0))
                except Exception:
                    try:
                        revenue = int(float(data.get("revenue", 0)))
                    except Exception:
                        revenue = 0

            results.append({
                "date": d.strftime("%Y-%m-%d"),
                "total": revenue
            })

        return jsonify({"store": store_name, "summary": results}), 200

    except Exception as e:
        return jsonify({"error": f"/get_sales_summary 失敗：{type(e).__name__}: {e}"}), 500

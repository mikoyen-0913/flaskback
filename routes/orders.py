# routes/orders.py
from datetime import datetime, timedelta, date
from typing import Optional, Any, List
import traceback

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
    return (dt_utc + timedelta(hours=8)).strftime("%Y%m%d")


# =========================
# 單位正規化 / 轉換
# =========================
UNIT_ALIAS = {
    "g": "克", "kg": "克",
    "ml": "毫升", "l": "毫升",
    "公克": "克", "公升": "毫升",
}

MULTIPLIER = {
    ("kg", "克"): 1000,
    ("l", "毫升"): 1000,
}

def normalize_unit(unit: Any) -> str:
    s = str(unit or "").strip()
    if not s:
        return s
    return UNIT_ALIAS.get(s.lower(), s)

def convert_amount(ingredient_unit: str, recipe_unit: str, amount: float) -> float:
    key = (ingredient_unit, recipe_unit)
    if key in MULTIPLIER:
        return amount / MULTIPLIER[key]
    elif (recipe_unit, ingredient_unit) in MULTIPLIER:
        return amount * MULTIPLIER[(recipe_unit, ingredient_unit)]
    return amount


# =========================
# ✅ 批次扣庫存（核心修正：兩階段交易）
# =========================
def _get_current_batch_id_safe_tx(transaction: firestore.Transaction, ing_ref, ing_data: dict) -> Optional[str]:
    cbid = ing_data.get("current_batch_id")
    if cbid:
        return cbid
    try:
        # 只抓取標準的 "in_use"
        q = ing_ref.collection("batches").where("status", "==", "in_use").limit(1)
        docs = list(transaction.get(q))
        return docs[0].id if docs else None
    except Exception:
        return None

def consume_ingredient_with_batches(store_name: str, ingredient_doc_id: str, amount_to_consume: float) -> None:
    """
    ✅ 兩階段交易 (Read / Write Phase) 以解決 Firestore 500 錯誤
    """
    if amount_to_consume is None:
        return
    try:
        amount_to_consume = float(amount_to_consume)
    except Exception:
        raise ValueError("扣庫存數量不是數字")
    if amount_to_consume <= 0:
        return

    ing_ref = (
        db.collection("stores")
          .document(store_name)
          .collection("ingredients")
          .document(ingredient_doc_id)
    )

    transaction = db.transaction()

    @firestore.transactional
    def _tx(transaction: firestore.Transaction):
        # 🟢 PHASE 1: 讀取階段 (Read Phase)
        ing_snap = ing_ref.get(transaction=transaction)
        if not ing_snap.exists:
            raise ValueError("ingredient not found")
        ing_data = ing_snap.to_dict() or {}
        ing_name = ing_data.get("name", ingredient_doc_id)

        # 決定從哪個批次開始
        current_batch_id = _get_current_batch_id_safe_tx(transaction, ing_ref, ing_data)
        
        # 收集所有需要用到的批次資料 [ (id, data, ref), ... ]
        batches_chain = []
        total_available = 0.0
        
        # 1. 先讀取當前批次 (in_use)
        if current_batch_id:
            b_ref = ing_ref.collection("batches").document(current_batch_id)
            b_snap = b_ref.get(transaction=transaction)
            if b_snap.exists:
                b_data = b_snap.to_dict()
                if b_data.get("status") == "in_use":
                    batches_chain.append((current_batch_id, b_data, b_ref))
                    total_available += float(b_data.get("quantity", 0) or 0)

        # 2. 如果不夠，讀取預備庫存 (unused)
        needed = float(amount_to_consume)
        
        if total_available < needed:
            # 抓取所有 unused
            # [FIX] 這裡只篩選 status，不使用 order_by，避免 500 錯誤或需要建索引
            q = ing_ref.collection("batches").where("status", "==", "unused")
            unused_docs = list(transaction.get(q))
            
            # [FIX] 在 Python 端進行排序 (FIFO: 依 created_at)
            def _sort_key(d):
                data = d.to_dict() or {}
                # 如果沒有 created_at，用 id 排，確保順序固定
                return data.get("created_at") or datetime.max

            unused_docs.sort(key=_sort_key)

            for doc in unused_docs:
                b_data = doc.to_dict()
                batches_chain.append((doc.id, b_data, doc.reference))
                total_available += float(b_data.get("quantity", 0) or 0)
                
                if total_available >= needed:
                    break
        
        # 3. 檢查總庫存
        if total_available < needed:
            raise ValueError(f"食材「{ing_name}」庫存不足！需求 {needed}，可用僅 {total_available}")

        # 🔴 PHASE 2: 寫入階段 (Write Phase)
        remaining_to_deduct = needed
        next_current_batch_id = None
        final_batch_qty = 0
        final_batch_exp = None
        
        for index, (b_id, b_data, b_ref) in enumerate(batches_chain):
            if remaining_to_deduct <= 0:
                break
            
            current_qty = float(b_data.get("quantity", 0) or 0)
            
            # 計算這一批要扣多少
            deduct_amount = min(current_qty, remaining_to_deduct)
            new_qty = current_qty - deduct_amount
            remaining_to_deduct -= deduct_amount
            
            # 決定狀態
            new_status = "in_use"
            if new_qty == 0:
                new_status = "depleted"
            else:
                # 這一批還有剩，它將成為新的 current_batch
                next_current_batch_id = b_id
                final_batch_qty = new_qty
                final_batch_exp = b_data.get("expiration_date")
            
            # 執行更新
            transaction.update(b_ref, {
                "quantity": new_qty,
                "status": new_status
            })
            
        # 更新父文件
        if next_current_batch_id:
            transaction.update(ing_ref, {
                "current_batch_id": next_current_batch_id,
                "quantity": final_batch_qty,
                "current_quantity": final_batch_qty,
                "expiration_date": final_batch_exp,
                "status": "in_stock"
            })
        else:
            # 剛好全部用完
            transaction.update(ing_ref, {
                "current_batch_id": None,
                "quantity": 0,
                "current_quantity": 0,
                "status": "out_of_stock"
            })

    _tx(transaction)


def _deduct_inventory_for_items(store_name: str, items: list[dict]) -> None:
    """
    items -> recipes -> ingredients 扣批次庫存
    """
    print(f"[扣庫存-開始] store={store_name} items_count={len(items)}")
    
    # [FIX] 修正路徑：讀取根目錄 recipes
    recipes_col = db.collection("recipes")

    for item in items:
        menu_id = item.get("menu_id")
        menu_name = item.get("menu_name")
        quantity = item.get("quantity", 1)

        print(f"[扣庫存-品項] {menu_name} (x{quantity})")

        try:
            quantity = float(quantity)
        except Exception:
            quantity = 1.0

        # 找 recipe
        recipe_doc = None
        tried = []

        if menu_id:
            tried.append(f"recipes/{menu_id}")
            snap = recipes_col.document(str(menu_id)).get()
            if snap.exists:
                recipe_doc = snap

        if not recipe_doc and menu_name:
            tried.append(f"recipes/{menu_name}")
            snap = recipes_col.document(str(menu_name)).get()
            if snap.exists:
                recipe_doc = snap

        if not recipe_doc:
            # 嘗試 where 查詢
            conds = []
            if menu_id: conds.append(("menu_id", menu_id))
            if menu_name: conds.append(("name", menu_name))
            
            for f, v in conds:
                try:
                    docs = list(recipes_col.where(f, "==", v).limit(1).stream())
                    if docs: 
                        recipe_doc = docs[0]
                        break
                except: pass

        if not recipe_doc or not getattr(recipe_doc, "exists", True):
            print(f"[扣庫存-失敗] 找不到 recipe；tried={tried}")
            raise ValueError(f"找不到產品「{menu_name}」的食譜設定(recipes)，無法扣庫存！")

        recipe_data = recipe_doc.to_dict() or {}
        ingredients_map = recipe_data.get("ingredients")
        if not isinstance(ingredients_map, dict):
            # 相容舊格式：直接把 recipe_data 當作 ingredients (排除非 dict 欄位)
            ingredients_map = {k: v for k, v in recipe_data.items() if isinstance(v, dict) and "amount" in v}

        if not ingredients_map:
            print(f"[扣庫存-略過] 食譜無食材設定：{menu_name}")
            continue

        for ing_name, detail in ingredients_map.items():
            amount = float((detail or {}).get("amount", 0) or 0)
            recipe_unit = normalize_unit((detail or {}).get("unit"))

            if amount <= 0:
                continue

            # 找庫存食材 (這是分店層級的)
            ing_query = (
                db.collection("stores").document(store_name)
                  .collection("ingredients")
                  .where("name", "==", ing_name)
                  .limit(1)
                  .stream()
            )
            ing_doc = next(ing_query, None)
            if not ing_doc:
                raise ValueError(f"食譜需要「{ing_name}」，但在 {store_name} 庫存中找不到！")

            ing_data = ing_doc.to_dict() or {}
            ingredient_unit = normalize_unit(ing_data.get("unit"))

            if recipe_unit != ingredient_unit:
                try:
                    adjusted_amount = convert_amount(ingredient_unit, recipe_unit, amount)
                except Exception:
                    raise ValueError(
                        f"{ing_name} 單位不符且無法轉換：食譜={recipe_unit}, 庫存={ingredient_unit}"
                    )
            else:
                adjusted_amount = amount

            need = float(adjusted_amount) * float(quantity)
            print(f"[扣庫存-執行] {ing_name} 需扣 {need} ({ingredient_unit}) doc_id={ing_doc.id}")

            # 執行扣庫存
            consume_ingredient_with_batches(
                store_name=store_name,
                ingredient_doc_id=ing_doc.id,
                amount_to_consume=need,
            )

    print(f"[扣庫存-結束] store={store_name}")


# =========================
# ✅ 核心下單邏輯 (共用)
# =========================
def _create_order_logic(store_name: str, items: List[dict]):
    """
    處理訂單建立的共用邏輯：計算金額、取號、寫入 DB
    """
    if not isinstance(items, list) or not items:
        raise ValueError("items 欄位必須為陣列且不可為空")

    order_items = []
    total_price = 0

    for item in items:
        menu_id = item.get("menu_id")
        quantity = item.get("quantity")

        if not menu_id or not isinstance(quantity, (int, float)):
            raise ValueError("每個項目必須含有 menu_id 和 quantity")

        menu_doc = db.collection("menus").document(menu_id).get()
        if not menu_doc.exists:
            raise ValueError(f"找不到菜單 {menu_id}")

        menu_data = menu_doc.to_dict()
        unit_price = menu_data.get("price", 0)
        subtotal = unit_price * quantity

        order_items.append({
            "menu_id": menu_id,
            "menu_name": menu_data.get("name", "未知品項"),
            "unit_price": unit_price,
            "quantity": quantity,
            "subtotal": subtotal
        })
        total_price += subtotal

    now = datetime.utcnow()
    date_str = taipei_today_str_from_utc(now)
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
    
    return doc_ref[1].id, order_number, order_data


# ----------------------------------------------------
# Running Total：把完成訂單即時累加到 daily_summary/summary
# ----------------------------------------------------
def _apply_order_to_running_total(store_name: str, ymd: str, completed_doc_id: str, order_data: dict):
    """
    將一筆 completed order 累加到 summary (冪等)
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
    flavor_increments = [] 

    # 允許使用 order 層級 total_price
    if isinstance(order_data.get("total_price"), (int, float)):
        total_price = int(order_data["total_price"])

    for it in items:
        mid = str(it.get("menu_id", "")).strip()
        mname = it.get("menu_name") or mid
        qty = 0
        sub = 0
        try:
            qty = int(it.get("quantity", 0))
        except: pass
        try:
            sub = int(it.get("subtotal", 0))
        except: pass

        total_qty += max(qty, 0)
        # 如果 order 沒 total_price，就由 items 累加
        if not isinstance(order_data.get("total_price"), (int, float)):
            total_price += max(sub, 0)

        if mid:
            flavor_increments.append((mid, mname, max(qty, 0), max(sub, 0)))

    @firestore.transactional
    def _txn(transaction: firestore.Transaction):
        # 防重
        applied_snap = applied_flag_ref.get(transaction=transaction)
        if applied_snap.exists:
            return "already_applied"

        # 確保 summary 基礎欄位
        base = {
            "store": store_name,
            "date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
            "monthKey": ymd[:6],
        }
        transaction.set(summary_ref, base, merge=True)

        # 更新
        updates = {
            "revenue": Increment(int(total_price)),
            "orders_count": Increment(1),
            "items_count": Increment(int(total_qty)),
            "last_updated_at": firestore.SERVER_TIMESTAMP,
        }
        for mid, mname, qty, sub in flavor_increments:
            updates[f"flavor_counts.{mid}"] = Increment(int(qty))
            updates[f"flavor_revenue.{mid}"] = Increment(int(sub))
            updates[f"flavor_labels.{mid}"] = mname

        transaction.update(summary_ref, updates)

        # 打旗標
        transaction.set(applied_flag_ref, {
            "order_id": completed_doc_id,
            "applied_at": firestore.SERVER_TIMESTAMP,
        }, merge=False)

        return "applied"

    tx = db.transaction()
    return _txn(tx)


# =========================
# API Routes
# =========================

@orders_bp.route('/place_order', methods=['POST'])
@token_required
def place_order():
    try:
        store_name = request.user.get("store_name")
        data = request.get_json()

        if "menu_id" in data and "quantity" in data:
            data = {"items": [{"menu_id": data["menu_id"], "quantity": data["quantity"]}]}

        order_id, order_num, order_data = _create_order_logic(store_name, data.get("items"))

        return jsonify({
            "message": "訂單成立成功",
            "order_id": order_id,
            "order_number": order_num,
            "order": order_data
        }), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        print("下單錯誤：", str(e))
        return jsonify({"error": str(e)}), 500


@orders_bp.route('/public_place_order', methods=['POST'])
def public_place_order():
    try:
        data = request.get_json()
        store_name = data.get("store_name")
        if not store_name:
            return jsonify({"error": "缺少 store_name"}), 400

        order_id, order_num, order_data = _create_order_logic(store_name, data.get("items"))

        return jsonify({
            "message": "訂單成立成功",
            "order_id": order_id,
            "order_number": order_num,
            "order": order_data
        }), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        print("下單錯誤：", str(e))
        return jsonify({"error": str(e)}), 500


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


@orders_bp.route('/delete_order/<order_id>', methods=['DELETE'])
@token_required
def delete_order(order_id):
    try:
        store_name = request.user.get("store_name")
        db.collection("stores").document(store_name).collection("orders").document(order_id).delete()
        return jsonify({"message": "訂單刪除成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@orders_bp.route("/complete_order/<order_id>", methods=["POST"])
@token_required
def complete_order(order_id):
    try:
        store_name = request.user.get("store_name")

        order_ref = db.collection("stores").document(store_name).collection("orders").document(order_id)
        order_doc = order_ref.get()
        if not order_doc.exists:
            return jsonify({"error": "訂單不存在"}), 404

        order_data = order_doc.to_dict() or {}
        items = order_data.get("items", [])

        # 1. 扣批次庫存 (Transaction 內已處理防呆)
        _deduct_inventory_for_items(store_name, items)

        # 2. 移動訂單到 completed_orders
        now_utc = datetime.utcnow()
        ymd = taipei_today_str_from_utc(now_utc)
        order_number = order_data.get("order_number", 0)
        doc_id = f"{ymd}-{order_number}"

        order_data["status"] = "completed"
        order_data["used_in_inventory_refresh"] = False
        order_data["completed_at"] = firestore.SERVER_TIMESTAMP
        order_data["timestamp"] = firestore.SERVER_TIMESTAMP
        order_data["store_name"] = store_name

        dates_ref = (db.collection("stores").document(store_name)
                        .collection("dates").document(ymd)
                        .collection("completed_orders").document(doc_id))
        dates_ref.set(order_data)

        # 3. Running Total：即時累加到當天 summary
        _apply_order_to_running_total(store_name, ymd, doc_id, order_data)

        # 4. 刪除 pending
        order_ref.delete()

        return jsonify({"message": "訂單已完成並已扣庫存"}), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        print(f"Complete Error: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@orders_bp.route("/complete_multiple_orders", methods=["POST"])
@token_required
def complete_multiple_orders():
    try:
        store_name = request.user.get("store_name")
        data = request.get_json()
        ids = data.get("ids", [])

        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "請提供要完成的訂單 ID 陣列"}), 400

        for order_id in ids:
            order_ref = db.collection("stores").document(store_name).collection("orders").document(order_id)
            order_doc = order_ref.get()
            if not order_doc.exists:
                continue

            order_data = order_doc.to_dict() or {}
            items = order_data.get("items", [])

            # 1. 扣庫存
            try:
                _deduct_inventory_for_items(store_name, items)
            except Exception as e:
                print(f"訂單 {order_id} 扣庫存失敗，跳過: {e}")
                continue

            # 2. 移動訂單
            now_utc = datetime.utcnow()
            ymd = taipei_today_str_from_utc(now_utc)
            order_number = order_data.get("order_number", 0)
            doc_id = f"{ymd}-{order_number}"

            order_data["status"] = "completed"
            order_data["used_in_inventory_refresh"] = False
            order_data["completed_at"] = firestore.SERVER_TIMESTAMP
            order_data["timestamp"] = firestore.SERVER_TIMESTAMP
            order_data["store_name"] = store_name

            dates_ref = (db.collection("stores").document(store_name)
                            .collection("dates").document(ymd)
                            .collection("completed_orders").document(doc_id))
            dates_ref.set(order_data)

            # 3. Running Total
            _apply_order_to_running_total(store_name, ymd, doc_id, order_data)

            # 4. 刪除
            order_ref.delete()

        return jsonify({"message": "多筆訂單完成成功"}), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
            order['id'] = doc.id
            orders.append(order)
        return jsonify({"date": date_str, "orders": orders}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@orders_bp.route("/get_sales_summary", methods=["GET"])
@token_required
def get_sales_summary():
    try:
        store_name = request.user.get("store_name")
        if not store_name:
            return jsonify({"error": "找不到 store_name"}), 400

        days_raw = request.args.get("days", "7")
        try:
            days = int(str(days_raw).strip())
        except:
            return jsonify({"error": "days error"}), 400
        if days not in (7, 14, 30):
            return jsonify({"error": "allowed: 7, 14, 30"}), 400

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
                except:
                    revenue = 0

            results.append({
                "date": d.strftime("%Y-%m-%d"),
                "total": revenue
            })

        return jsonify({"store": store_name, "summary": results}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
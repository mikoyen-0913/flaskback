from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required
from google.cloud import firestore
from datetime import datetime
from zoneinfo import ZoneInfo
import os

TZ = ZoneInfo("Asia/Taipei")

ingredients_bp = Blueprint('ingredients', __name__)
ingredients_collection = "ingredients"

# ==========================
# 共用工具
# ==========================
UNIT_ALIAS = {"g": "克", "kg": "克", "ml": "毫升", "l": "毫升", "公克": "克", "公升": "毫升"}
MULTIPLIER = {("kg", "克"): 1000, ("l", "毫升"): 1000}

def normalize_unit(u):
    s = str(u or "").strip()
    return UNIT_ALIAS.get(s.lower(), s)

def convert_amount(ingredient_unit, recipe_unit, amount):
    """把『食譜單位』的 amount 轉成『庫存單位』數值。"""
    key = (ingredient_unit, recipe_unit)
    if key in MULTIPLIER:
        # 庫存(kg) -> 食譜(克) 的相反：食譜量 / 1000
        return amount / MULTIPLIER[key]
    elif (recipe_unit, ingredient_unit) in MULTIPLIER:
        # 食譜(kg) -> 庫存(克)：食譜量 * 1000
        return amount * MULTIPLIER[(recipe_unit, ingredient_unit)]
    return amount

def _refresh_for_store_today(store_name: str):
    """
    處理單一門市今天的訂單扣庫存。
    回傳 (ingredient_deduction_dict, processed_orders_count, YYYYMMDD)
    """
    today_str = datetime.now(TZ).strftime("%Y%m%d")
    completed_orders_ref = (
        db.collection("stores")
          .document(store_name)
          .collection("dates")
          .document(today_str)
          .collection("completed_orders")
    )
    orders = completed_orders_ref.where("used_in_inventory_refresh", "==", False).stream()

    sales_count = {}
    processed_ids = []

    for doc in orders:
        order = doc.to_dict() or {}
        processed_ids.append(doc.id)
        for item in order.get("items", []):
            name = item.get("menu_name")
            qty = item.get("quantity", 0)
            if not name:
                continue
            try:
                qty = float(qty)
            except Exception:
                qty = 0
            sales_count[name] = sales_count.get(name, 0) + qty

    ingredient_deduction = {}

    for menu_name, total_qty in sales_count.items():
        recipe_doc = db.collection("recipes").document(menu_name).get()
        if not recipe_doc.exists:
            continue
        recipe = recipe_doc.to_dict() or {}

        for ing_name, detail in recipe.items():
            recipe_amt = float(detail.get("amount", 0) or 0)
            recipe_unit = normalize_unit(detail.get("unit", ""))

            ing_query = (
                db.collection("stores")
                  .document(store_name)
                  .collection("ingredients")
                  .where("name", "==", ing_name)
                  .limit(1)
            ).stream()

            for ing_doc in ing_query:
                ing_id = ing_doc.id
                ing_data = ing_doc.to_dict() or {}
                ing_unit = normalize_unit(ing_data.get("unit", ""))

                adjusted_amt = convert_amount(ing_unit, recipe_unit, recipe_amt) if recipe_unit != ing_unit else recipe_amt
                final_deduction = float(adjusted_amt) * float(total_qty)

                ingredient_deduction[ing_id] = ingredient_deduction.get(ing_id, 0) + final_deduction

    # 扣庫存
    for ing_id, deduction in ingredient_deduction.items():
        ing_ref = (
            db.collection("stores")
              .document(store_name)
              .collection("ingredients")
              .document(ing_id)
        )
        ing_ref.update({"quantity": firestore.Increment(-deduction)})

    # 標記訂單已用於扣庫存
    for doc_id in processed_ids:
        completed_orders_ref.document(doc_id).update({"used_in_inventory_refresh": True})

    return ingredient_deduction, len(processed_ids), today_str

# ==========================
# API: 食材 CRUD
# ==========================
@ingredients_bp.route('/get_ingredients', methods=['GET'])
@token_required
def get_ingredients():
    try:
        store_name = request.user.get("store_name")
        ingredients_ref = db.collection("stores").document(store_name).collection("ingredients").stream()
        ingredients = [{"id": ing.id, **ing.to_dict()} for ing in ingredients_ref]
        return jsonify({"ingredients": ingredients}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@ingredients_bp.route('/add_ingredient', methods=['POST'])
@token_required
def add_ingredient():
    try:
        if request.user.get("role") != "developer":
            return jsonify({"error": "無權限新增食材"}), 403

        data = request.get_json()
        print("接收到的新增食材資料:", data)

        required_fields = ["name", "quantity", "unit", "price", "expiration_date"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"缺少必要欄位：{field}"}), 400

        allowed_units = ["克", "毫升"]
        if data["unit"] not in allowed_units:
            return jsonify({"error": "無效的單位，請選擇 '克' 或 '毫升'"}), 400

        store_name = request.user.get("store_name")
        ingredients_ref = db.collection("stores").document(store_name).collection("ingredients")

        # 查是否已有相同名稱
        query = ingredients_ref.where("name", "==", data["name"]).limit(1).stream()
        existing_doc = next(query, None)

        if existing_doc:
            existing_data = existing_doc.to_dict() or {}
            doc_id = existing_doc.id
            new_quantity = existing_data.get("quantity", 0) + data["quantity"]
            updated_price = existing_data.get("price", 0) or data["price"]

            ingredients_ref.document(doc_id).update({
                "quantity": new_quantity,
                "expiration_date": data["expiration_date"],
                "price": updated_price
            })
            return jsonify({"message": "食材已合併更新", "doc_id": doc_id}), 200
        else:
            new_doc = ingredients_ref.add({
                "name": data["name"],
                "quantity": data["quantity"],
                "unit": data["unit"],
                "price": data["price"],
                "expiration_date": data["expiration_date"]
            })
            return jsonify({"message": "食材新增成功", "doc_id": new_doc[1].id}), 200

    except Exception as e:
        print("錯誤訊息:", str(e))
        return jsonify({"error": str(e)}), 500

@ingredients_bp.route('/update_ingredient/<ingredient_id>', methods=['PUT'])
@token_required
def update_ingredient(ingredient_id):
    try:
        data = request.get_json()
        updatable_fields = ["name", "quantity", "unit", "price", "expiration_date"]
        update_data = {key: data[key] for key in updatable_fields if key in data}
        if not update_data:
            return jsonify({"error": "沒有提供更新資料"}), 400

        store_name = request.user.get("store_name")
        db.collection("stores").document(store_name).collection("ingredients").document(ingredient_id).update(update_data)
        return jsonify({"message": "食材更新成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@ingredients_bp.route('/delete_ingredient/<ingredient_id>', methods=['DELETE'])
@token_required
def delete_ingredient(ingredient_id):
    try:
        store_name = request.user.get("store_name")
        db.collection("stores").document(store_name).collection("ingredients").document(ingredient_id).delete()
        return jsonify({"message": "食材刪除成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==========================
# API: 補貨
# ==========================
@ingredients_bp.route('/restock_ingredients', methods=['POST'])
@token_required
def restock_ingredients():
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "資料格式錯誤"}), 400

        store_name = request.user.get("store_name")

        for ing_id, info in data.items():
            restock_amount = info.get("restock", 0)
            if not isinstance(restock_amount, (int, float)) or restock_amount <= 0:
                continue

            doc_ref = db.collection("stores").document(store_name).collection("ingredients").document(ing_id)
            doc = doc_ref.get()
            if not doc.exists:
                continue

            current_quantity = doc.to_dict().get("quantity", 0)
            new_quantity = current_quantity + restock_amount
            doc_ref.update({"quantity": new_quantity})

        return jsonify({"message": "補貨成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==========================
# API: 只扣今天訂單（單一門市，需登入）
# ==========================
@ingredients_bp.route('/refresh_inventory_by_sales', methods=['POST'])
@token_required
def refresh_inventory_by_sales():
    try:
        store_name = request.user.get("store_name")
        deducted, count, today = _refresh_for_store_today(store_name)
        return jsonify({
            "message": f"{today} 即時庫存已更新",
            "processed_orders": count,
            "deducted_ingredients": deducted
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==========================
# API: Cron 用（全門市）
# ==========================
@ingredients_bp.route('/cron/refresh_today', methods=['POST'])
def cron_refresh_today():
    """排程用：刷新所有門市『今天』的訂單"""
    cron_key = request.headers.get("X-CRON-KEY")
    if cron_key != os.environ.get("CRON_KEY"):
        return jsonify({"error": "unauthorized"}), 401

    results = {}
    today = datetime.now(TZ).strftime("%Y%m%d")

    for store_doc in db.collection("stores").stream():
        store_name = store_doc.id
        try:
            deducted, count, _ = _refresh_for_store_today(store_name)
            results[store_name] = {
                "processed_orders": count,
                "deducted_ingredients": deducted
            }
        except Exception as e:
            results[store_name] = {"error": str(e)}

    return jsonify({"date": today, "results": results}), 200

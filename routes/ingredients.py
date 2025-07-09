from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required
from google.cloud import firestore


ingredients_bp = Blueprint('ingredients', __name__)
ingredients_collection = "ingredients"

# ✅ 取得所有食材（需登入）
@ingredients_bp.route('/get_ingredients', methods=['GET'])
@token_required
def get_ingredients():
    try:
        store_name = request.user.get("store_name")
        ingredients = []
        ingredients_ref = db.collection("stores").document(store_name).collection("ingredients").stream()

        for ing in ingredients_ref:
            try:
                ingredients.append({
                    "id": ing.id,
                    "name": ing.get("name"),
                    "quantity": ing.get("quantity"),
                    "unit": ing.get("unit"),
                    "price": ing.get("price"),
                    "expiration_date": str(ing.get("expiration_date"))  # 避免 DateTimeWithNanoseconds
                })
            except Exception as e:
                print(f"⚠️ 食材解析錯誤：{e}")

        return jsonify({"ingredients": ingredients}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@ingredients_bp.route('/add_ingredient', methods=['POST'])
@token_required
def add_ingredient():
    try:
        # ✅ 權限檢查
        if request.user.get("role") != "developer":
            return jsonify({"error": "無權限新增食材"}), 403

        # ✅ 資料驗證
        data_list = request.get_json()
        if not isinstance(data_list, list):
            return jsonify({"error": "請傳入 JSON 陣列"}), 400

        store_name = request.user.get("store_name")
        allowed_units = ["克", "毫升"]
        added_ids = []

        # ✅ 一筆一筆新增資料
        for idx, data in enumerate(data_list):
            required_fields = ["name", "quantity", "unit", "price", "expiration_date"]
            for field in required_fields:
                if field not in data:
                    return jsonify({"error": f"第 {idx+1} 筆資料缺少欄位：{field}"}), 400

            if data["unit"] not in allowed_units:
                return jsonify({"error": f"第 {idx+1} 筆資料單位錯誤，僅支援 '克' 或 '毫升'"}), 400

            # ✅ 新增到 Firestore
            doc_ref, _ = db.collection("stores").document(store_name).collection("ingredients").add({
                "name": data.get("name"),
                "quantity": data.get("quantity"),
                "unit": data.get("unit"),
                "price": data.get("price"),
                "expiration_date": data.get("expiration_date")
            })
            added_ids.append(doc_ref.id)

        # ✅ 撈出所有資料
        ingredients = []
        ingredients_ref = db.collection("stores").document(store_name).collection("ingredients").stream()
        for doc in ingredients_ref:
            try:
                ing_dict = doc.to_dict()
                exp = ing_dict.get("expiration_date")
                # 安全轉換 expiration_date 為字串
                if hasattr(exp, "isoformat"):
                    expiration_str = exp.isoformat()
                else:
                    expiration_str = str(exp) if exp else "無效日期"

                # 加入清單
                ingredients.append({
                    "id": doc.id,
                    "name": ing_dict.get("name", ""),
                    "quantity": ing_dict.get("quantity", 0),
                    "unit": ing_dict.get("unit", ""),
                    "price": ing_dict.get("price", 0),
                    "expiration_date": expiration_str
                })
            except Exception as e:
                print(f"⚠️ 無法解析食材 document：{str(e)}")
                continue  # 避免中斷整體處理

        return jsonify({
            "message": f"成功新增 {len(added_ids)} 筆食材",
            "added_ids": added_ids,
            "ingredients": ingredients
        }), 200

    except Exception as e:
        print("❌ 錯誤訊息:", str(e))
        return jsonify({"error": str(e)}), 500

# ✅ 更新食材
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

# ✅ 刪除食材
@ingredients_bp.route('/delete_ingredient/<ingredient_id>', methods=['DELETE'])
@token_required
def delete_ingredient(ingredient_id):
    try:
        store_name = request.user.get("store_name")
        db.collection("stores").document(store_name).collection("ingredients").document(ingredient_id).delete()
        return jsonify({"message": "食材刪除成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 補貨
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

@ingredients_bp.route('/refresh_inventory_by_sales', methods=['POST'])
@token_required
def refresh_inventory_by_sales():
    try:
        store_name = request.user.get("store_name")
        completed_orders_ref = db.collection("stores").document(store_name).collection("completed_orders")
        orders = completed_orders_ref.where("used_in_inventory_refresh", "==", False).stream()

        sales_count = {}
        processed_ids = []

        # 統計所有已完成未處理訂單的 menu_name 賣出數量
        for doc in orders:
            order = doc.to_dict()
            processed_ids.append(doc.id)
            for item in order.get("items", []):
                name = item.get("menu_name")
                qty = item.get("quantity", 0)
                if name in sales_count:
                    sales_count[name] += qty
                else:
                    sales_count[name] = qty

        # 讀取 recipes 並換算成食材需求量
        UNIT_ALIAS = {"g": "克", "kg": "克", "ml": "毫升", "l": "毫升", "公克": "克", "公升": "毫升"}
        MULTIPLIER = {("kg", "克"): 1000, ("l", "毫升"): 1000}

        def normalize_unit(u): return UNIT_ALIAS.get(u.strip().lower(), u.strip())
        def convert_amount(ingredient_unit, recipe_unit, amount):
            key = (ingredient_unit, recipe_unit)
            if key in MULTIPLIER:
                return amount / MULTIPLIER[key]
            elif (recipe_unit, ingredient_unit) in MULTIPLIER:
                return amount * MULTIPLIER[(recipe_unit, ingredient_unit)]
            return amount

        ingredient_deduction = {}

        for menu_name, total_qty in sales_count.items():
            recipe_doc = db.collection("recipes").document(menu_name).get()
            if not recipe_doc.exists:
                continue
            recipe = recipe_doc.to_dict()
            for ing_name, detail in recipe.items():
                recipe_amt = detail.get("amount", 0)
                recipe_unit = normalize_unit(detail.get("unit", ""))

                ing_query = db.collection("stores").document(store_name).collection("ingredients").where("name", "==", ing_name).limit(1).stream()
                for ing_doc in ing_query:
                    ing_id = ing_doc.id
                    ing_data = ing_doc.to_dict()
                    ing_unit = normalize_unit(ing_data.get("unit", ""))

                    if recipe_unit != ing_unit:
                        try:
                            adjusted_amt = convert_amount(ing_unit, recipe_unit, recipe_amt)
                        except:
                            return jsonify({"error": f"{ing_name} 單位不符且無法轉換"}), 400
                    else:
                        adjusted_amt = recipe_amt

                    final_deduction = adjusted_amt * total_qty
                    ingredient_deduction[ing_id] = ingredient_deduction.get(ing_id, 0) + final_deduction

        # 更新 ingredients 的數量
        for ing_id, deduction in ingredient_deduction.items():
            ing_ref = db.collection("stores").document(store_name).collection("ingredients").document(ing_id)
            ing_ref.update({"quantity": firestore.Increment(-deduction)})

        # 將處理過的 completed_orders 記錄為 used
        for doc_id in processed_ids:
            completed_orders_ref.document(doc_id).update({"used_in_inventory_refresh": True})

        return jsonify({
            "message": "即時庫存已更新",
            "deducted_ingredients": ingredient_deduction
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

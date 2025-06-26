from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required

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
        # 檢查是否為 developer 權限
        if request.user.get("role") != "developer":
            return jsonify({"error": "無權限新增食材"}), 403

        # 解析前端傳來的資料
        data = request.get_json()
        print("接收到的新增食材資料:", data)

        # 必要欄位驗證
        required_fields = ["name", "quantity", "unit", "price", "expiration_date"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"缺少必要欄位：{field}"}), 400

        # 檢查單位是否合法
        allowed_units = ["克", "毫升"]
        if data["unit"] not in allowed_units:
            return jsonify({"error": "無效的單位，請選擇 '克' 或 '毫升'"}), 400

        # 取得店名
        store_name = request.user.get("store_name")

        # 寫入 Firestore
        doc_ref, _ = db.collection("stores").document(store_name).collection("ingredients").add({
            "name": data["name"],
            "quantity": data["quantity"],
            "unit": data["unit"],
            "price": data["price"],
            "expiration_date": data["expiration_date"]
        })

        # ✅ 新增成功後重新讀取所有食材
        ingredients = []
        ingredients_ref = db.collection("stores").document(store_name).collection("ingredients").stream()
        for ing in ingredients_ref:
            try:
                ing_dict = ing.to_dict()
                ingredients.append({
                    "id": ing.id,
                    "name": ing_dict.get("name"),
                    "quantity": ing_dict.get("quantity"),
                    "unit": ing_dict.get("unit"),
                    "price": ing_dict.get("price"),
                    "expiration_date": ing_dict.get("expiration_date").isoformat()
                })
            except Exception as e:
                print(f"⚠️ 無法解析食材 {ing.id}: {e}")

        return jsonify({
            "message": "食材新增成功",
            "doc_id": doc_ref.id,
            "ingredients": ingredients
        }), 200

    except Exception as e:
        print("錯誤訊息:", str(e))
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

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
        ingredients_ref = db.collection(ingredients_collection).stream()
        ingredients = [{"id": ing.id, **ing.to_dict()} for ing in ingredients_ref]
        return jsonify({"ingredients": ingredients}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 新增食材（需登入）
@ingredients_bp.route('/add_ingredient', methods=['POST'])
@token_required
def add_ingredient():
    try:
        data = request.get_json()

        # 驗證必要欄位
        required_fields = ["name", "quantity", "unit", "price", "expiration_date"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"缺少必要欄位：{field}"}), 400

        # 新增資料
        doc_ref = db.collection(ingredients_collection).add({
            "name": data["name"],
            "quantity": data["quantity"],
            "unit": data["unit"],
            "price": data["price"],
            "expiration_date": data["expiration_date"]
        })

        return jsonify({"message": "食材新增成功", "doc_id": doc_ref[1].id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 更新食材（需登入）
@ingredients_bp.route('/update_ingredient/<ingredient_id>', methods=['PUT'])
@token_required
def update_ingredient(ingredient_id):
    try:
        data = request.get_json()

        # 可選擇更新欄位（後端不強制驗證所有欄位）
        updatable_fields = ["name", "quantity", "unit", "price", "expiration_date"]
        update_data = {key: data[key] for key in updatable_fields if key in data}

        if not update_data:
            return jsonify({"error": "沒有提供更新資料"}), 400

        db.collection(ingredients_collection).document(ingredient_id).update(update_data)
        return jsonify({"message": "食材更新成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 刪除食材（需登入）
@ingredients_bp.route('/delete_ingredient/<ingredient_id>', methods=['DELETE'])
@token_required
def delete_ingredient(ingredient_id):
    try:
        db.collection(ingredients_collection).document(ingredient_id).delete()
        return jsonify({"message": "食材刪除成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 補貨功能（需登入）
@ingredients_bp.route('/restock_ingredients', methods=['POST'])
@token_required
def restock_ingredients():
    try:
        data = request.get_json()  # { "abc123": { "restock": 5 }, ... }

        if not isinstance(data, dict):
            return jsonify({"error": "資料格式錯誤"}), 400

        for ing_id, info in data.items():
            restock_amount = info.get("restock", 0)
            if not isinstance(restock_amount, (int, float)) or restock_amount <= 0:
                continue

            doc_ref = db.collection(ingredients_collection).document(ing_id)
            doc = doc_ref.get()
            if not doc.exists:
                continue

            current_quantity = doc.to_dict().get("quantity", 0)
            new_quantity = current_quantity + restock_amount

            doc_ref.update({"quantity": new_quantity})

        return jsonify({"message": "補貨成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

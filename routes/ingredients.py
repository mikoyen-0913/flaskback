from flask import Blueprint, request, jsonify
from firebase_config import db

ingredients_bp = Blueprint('ingredients', __name__)
ingredients_collection = "ingredients"

# ✅ 取得所有食材
@ingredients_bp.route('/get_ingredients', methods=['GET'])
def get_ingredients():
    try:
        ingredients_ref = db.collection(ingredients_collection).stream()
        ingredients = [{"id": ing.id, **ing.to_dict()} for ing in ingredients_ref]
        return jsonify({"ingredients": ingredients}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 新增食材
@ingredients_bp.route('/add_ingredient', methods=['POST'])
def add_ingredient():
    try:
        data = request.get_json()
        if not data.get("name") or "quantity" not in data or "price" not in data or "unit" not in data:
            return jsonify({"error": "缺少必要字段"}), 400
        doc_ref = db.collection(ingredients_collection).add(data)
        return jsonify({"message": "食材新增成功", "doc_id": doc_ref[1].id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 更新食材
@ingredients_bp.route('/update_ingredient/<ingredient_id>', methods=['PUT'])
def update_ingredient(ingredient_id):
    try:
        data = request.get_json()
        db.collection(ingredients_collection).document(ingredient_id).update(data)
        return jsonify({"message": "食材更新成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 刪除食材
@ingredients_bp.route('/delete_ingredient/<ingredient_id>', methods=['DELETE'])
def delete_ingredient(ingredient_id):
    try:
        db.collection(ingredients_collection).document(ingredient_id).delete()
        return jsonify({"message": "食材刪除成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 🔵 新增：補貨功能
@ingredients_bp.route('/restock_ingredients', methods=['POST'])
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

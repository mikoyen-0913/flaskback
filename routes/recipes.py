from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required

recipes_bp = Blueprint("recipes", __name__)
RECIPE_COLLECTION = "recipes"

# ✅ 取得所有配方
@recipes_bp.route("/recipes", methods=["GET"])
@token_required
def get_all_recipes():
    try:
        docs = db.collection(RECIPE_COLLECTION).stream()
        recipes = {doc.id: doc.to_dict() for doc in docs}
        return jsonify({"recipes": recipes}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 取得單一配方
@recipes_bp.route("/recipes/<menu_name>", methods=["GET"])
@token_required
def get_recipe(menu_name):
    try:
        doc = db.collection(RECIPE_COLLECTION).document(menu_name).get()
        if not doc.exists:
            return jsonify({"error": "此配方不存在"}), 404
        return jsonify({"recipe": doc.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 新增或更新配方
@recipes_bp.route("/recipes", methods=["POST"])
@token_required
def create_or_update_recipe():
    try:
        data = request.get_json()
        menu_name = data.get("menu_name")
        ingredients = data.get("ingredients")

        if not menu_name or not isinstance(ingredients, dict):
            return jsonify({"error": "缺少 menu_name 或 ingredients 欄位"}), 400

        for ing, detail in ingredients.items():
            if "amount" not in detail or "unit" not in detail:
                return jsonify({"error": f"{ing} 缺少 amount 或 unit"}), 400

        db.collection(RECIPE_COLLECTION).document(menu_name).set(ingredients)
        return jsonify({"message": f"{menu_name} 配方已儲存"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 刪除配方
@recipes_bp.route("/recipes/<menu_name>", methods=["DELETE"])
@token_required
def delete_recipe(menu_name):
    try:
        db.collection(RECIPE_COLLECTION).document(menu_name).delete()
        return jsonify({"message": f"{menu_name} 配方已刪除"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

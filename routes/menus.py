from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required


menus_bp = Blueprint('menus', __name__)
menus_collection = "menus"

# ✅ 取得所有菜單項目（需要登入）
@menus_bp.route('/get_menus', methods=['GET'])
@token_required
def get_menus():
    try:
        menus_ref = db.collection(menus_collection).stream()
        menus = [{"id": doc.id, **doc.to_dict()} for doc in menus_ref]
        return jsonify({"menus": menus}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 新增菜單項目（需要登入）
@menus_bp.route('/add_menu', methods=['POST'])
@token_required
def add_menu():
    try:
        data = request.get_json()
        if not data.get("name") or "price" not in data:
            return jsonify({"error": "缺少 name 或 price"}), 400

        db.collection(menus_collection).add(data)
        return jsonify({"message": "菜單新增成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 更新菜單項目（需要登入）
@menus_bp.route('/update_menu/<menu_id>', methods=['PUT'])
@token_required
def update_menu(menu_id):
    try:
        data = request.get_json()
        db.collection(menus_collection).document(menu_id).update(data)
        return jsonify({"message": "菜單更新成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 刪除菜單項目（需要登入）
@menus_bp.route('/delete_menu/<menu_id>', methods=['DELETE'])
@token_required
def delete_menu(menu_id):
    try:
        db.collection(menus_collection).document(menu_id).delete()
        return jsonify({"message": "菜單刪除成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

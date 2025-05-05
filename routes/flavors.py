from flask import Blueprint, request, jsonify
from firebase_config import db

flavors_bp = Blueprint('flavors', __name__)
flavors_collection = "flavors"

@flavors_bp.route('/add_flavor', methods=['POST'])
def add_flavor():
    try:
        data = request.get_json()
        if not data.get("name") or "ingredients" not in data:
            return jsonify({"error": "缺少必要字段"}), 400
        doc_ref = db.collection(flavors_collection).add(data)
        return jsonify({"message": "口味新增成功", "doc_id": doc_ref[1].id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@flavors_bp.route('/update_flavor/<flavor_id>', methods=['PUT'])
def update_flavor(flavor_id):
    try:
        data = request.get_json()
        db.collection(flavors_collection).document(flavor_id).update(data)
        return jsonify({"message": "口味更新成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@flavors_bp.route('/delete_flavor/<flavor_id>', methods=['DELETE'])
def delete_flavor(flavor_id):
    try:
        db.collection(flavors_collection).document(flavor_id).delete()
        return jsonify({"message": "口味刪除成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@flavors_bp.route('/get_flavors', methods=['GET'])
def get_flavors():
    try:
        flavors_ref = db.collection(flavors_collection).stream()
        flavors = [{"id": flavor.id, **flavor.to_dict()} for flavor in flavors_ref]
        return jsonify({"flavors": flavors}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

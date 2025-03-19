from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore

# 初始化 Flask 應用
app = Flask(__name__)
CORS(app)  # 允許跨域請求

# 初始化 Firebase Admin
cred = credentials.Certificate("yaoyaoproject-88907-firebase-adminsdk-fbsvc-e65f9829cc.json")
firebase_admin.initialize_app(cred)

# 連接 Firestore
db = firestore.client()
ingredients_collection = "ingredients"  # 食材集合
flavors_collection = "flavors"  # 口味集合

@app.route('/')
def home():
    return "紅豆餅店庫存管理系統後端運行中..."

# 新增食材 API
@app.route('/add_ingredient', methods=['POST'])
def add_ingredient():
    try:
        data = request.get_json()
        if not data.get("name") or "quantity" not in data or "price" not in data or "unit" not in data:
            return jsonify({"error": "缺少必要字段"}), 400
        doc_ref = db.collection(ingredients_collection).add(data)
        return jsonify({"message": "食材新增成功", "doc_id": doc_ref[1].id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 更新食材 API
@app.route('/update_ingredient/<ingredient_id>', methods=['PUT'])
def update_ingredient(ingredient_id):
    try:
        data = request.get_json()
        db.collection(ingredients_collection).document(ingredient_id).update(data)
        return jsonify({"message": "食材更新成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 刪除食材 API
@app.route('/delete_ingredient/<ingredient_id>', methods=['DELETE'])
def delete_ingredient(ingredient_id):
    try:
        db.collection(ingredients_collection).document(ingredient_id).delete()
        return jsonify({"message": "食材刪除成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 新增口味 API
@app.route('/add_flavor', methods=['POST'])
def add_flavor():
    try:
        data = request.get_json()
        if not data.get("name") or "ingredients" not in data:
            return jsonify({"error": "缺少必要字段"}), 400
        doc_ref = db.collection(flavors_collection).add(data)
        return jsonify({"message": "口味新增成功", "doc_id": doc_ref[1].id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 更新口味 API
@app.route('/update_flavor/<flavor_id>', methods=['PUT'])
def update_flavor(flavor_id):
    try:
        data = request.get_json()
        db.collection(flavors_collection).document(flavor_id).update(data)
        return jsonify({"message": "口味更新成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 刪除口味 API
@app.route('/delete_flavor/<flavor_id>', methods=['DELETE'])
def delete_flavor(flavor_id):
    try:
        db.collection(flavors_collection).document(flavor_id).delete()
        return jsonify({"message": "口味刪除成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 訂單 API（自動扣除食材庫存）
@app.route('/place_order', methods=['POST'])
def place_order():
    try:
        data = request.get_json()
        if not data.get("flavor_id") or "quantity" not in data:
            return jsonify({"error": "缺少必要字段"}), 400

        # 取得口味資訊
        flavor_ref = db.collection(flavors_collection).document(data["flavor_id"]).get()
        if not flavor_ref.exists:
            return jsonify({"error": "口味不存在"}), 404
        flavor_data = flavor_ref.to_dict()

        # 檢查並扣除食材庫存
        for ingredient in flavor_data["ingredients"]:
            ingredient_ref = db.collection(ingredients_collection).document(ingredient["ingredient_id"])
            ingredient_doc = ingredient_ref.get()
            if not ingredient_doc.exists:
                return jsonify({"error": f"食材 {ingredient['ingredient_id']} 不存在"}), 404

            ingredient_data = ingredient_doc.to_dict()
            new_quantity = ingredient_data["quantity"] - (ingredient["amount"] * data["quantity"])
            if new_quantity < 0:
                return jsonify({"error": f"食材 {ingredient_data['name']} 庫存不足"}), 400

            ingredient_ref.update({"quantity": new_quantity})

        return jsonify({"message": "訂單處理成功，食材已扣除"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 啟動 Flask 伺服器
if __name__ == '__main__':
    app.run(debug=True)

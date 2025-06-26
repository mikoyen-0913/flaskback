from flask import Flask, request, jsonify
from flask_cors import CORS
from firebase_config import db
from google.cloud import firestore

# 載入 Blueprint 模組
from routes.ingredients import ingredients_bp
from routes.flavors import flavors_bp
from routes.orders import orders_bp
from routes.menus import menus_bp
from routes.auth import auth_bp
from routes.recipes import recipes_bp

app = Flask(__name__)
CORS(app)  # ✅ 允許跨域，讓前端能呼叫後端 API

# 註冊 Blueprint
app.register_blueprint(ingredients_bp)
app.register_blueprint(flavors_bp)
app.register_blueprint(orders_bp)
app.register_blueprint(menus_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(recipes_bp)

# ✅ 公開 API：無需登入可下單
@app.route('/public_place_order', methods=['POST'])
def public_place_order():
    try:
        data = request.get_json()
        items = data.get("items", [])

        if not isinstance(items, list) or not items:
            return jsonify({"error": "items 欄位必須為非空陣列"}), 400

        order_ref = db.collection("users").document("dev011").collection("orders").document()
        order_ref.set({
            "items": items,
            "timestamp": firestore.SERVER_TIMESTAMP
        })

        return jsonify({"message": "訂單已建立", "order_id": order_ref.id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/public_menus', methods=['GET'])
def public_menus():
    try:
        menus = []
        for doc in db.collection("menus").stream():
            item = doc.to_dict()
            item["menu_id"] = doc.id  # 傳回 menu_id 給前端
            menus.append(item)
        return jsonify({"menus": menus})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    with app.test_request_context():
        print("✅ Registered Routes:")
        print(app.url_map)

    app.run(debug=True, use_reloader=False)

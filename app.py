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
from routes.inventory_checker import inventory_bp
from routes.superadmin import superadmin_bp

app = Flask(__name__)

# CORS 設定（允許本地端與 Firebase，支援 PUT/DELETE）
CORS(app, supports_credentials=True, origins=[
    "http://localhost:3000",
    "https://yaoyaoproject-88907.web.app"
], allow_headers=["Content-Type", "Authorization"],
   methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

# 註冊 Blueprint
app.register_blueprint(superadmin_bp)
app.register_blueprint(ingredients_bp)
app.register_blueprint(flavors_bp)
app.register_blueprint(orders_bp)
app.register_blueprint(menus_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(recipes_bp)
app.register_blueprint(inventory_bp)

# ngrok 防濫用提示避免
@app.after_request
def add_ngrok_header(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# favicon.ico 避免錯誤
@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route("/")
def home():
    return jsonify({"message": "Backend is running!"})

# 公開 API：無需登入即可下單
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

# 公開 API：無需登入即可取得菜單
@app.route('/public_menus', methods=['GET'])
def public_menus():
    try:
        menus = []
        for doc in db.collection("menus").stream():
            item = doc.to_dict()
            item["menu_id"] = doc.id
            menus.append(item)
        return jsonify({"menus": menus})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 主程式
if __name__ == '__main__':
    with app.test_request_context():
        print("✅ Registered Routes:")
        print(app.url_map)

    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

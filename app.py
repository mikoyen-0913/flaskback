import os, certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
os.environ.setdefault("CURL_CA_BUNDLE", certifi.where())
# app.py — Render-ready
import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.cloud import firestore

from firebase_config import db

# ===== 載入 Blueprint 模組 =====
from routes.ingredients import ingredients_bp
from routes.flavors import flavors_bp
from routes.orders import orders_bp
from routes.menus import menus_bp
from routes.auth import auth_bp
from routes.recipes import recipes_bp
from routes.inventory_checker import inventory_bp
from routes.superadmin import superadmin_bp

app = Flask(__name__)

# ===== CORS 設定（用環境變數 ALLOWED_ORIGINS 控制）=====
# 例：ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000,http://192.168.100.7:3000,https://你的前端站.onrender.com"
_default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://192.168.100.7:3000",
    " http://163.13.14.137:3000",   # 後端自己（可留可去，無妨）
    " http://163.13.54.132:3000 ",
    "http://192.168.100.6:3000 ",
    "http://163.13.48.116:3000",
    # "https://你的前端站名.onrender.com",  # 前端上線後記得加進來
]
_allowed = os.getenv("ALLOWED_ORIGINS", ",".join(_default_origins))
ALLOWED_ORIGINS = [o.strip() for o in _allowed.split(",") if o.strip()]

CORS(
    app,
    supports_credentials=True,
    origins=ALLOWED_ORIGINS,
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# ===== 註冊 Blueprint =====
app.register_blueprint(superadmin_bp)
app.register_blueprint(ingredients_bp)
app.register_blueprint(flavors_bp)
app.register_blueprint(orders_bp)
app.register_blueprint(menus_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(recipes_bp)
app.register_blueprint(inventory_bp)

# ===== 小工具：NGROK 防濫用提示（保留不影響）=====
@app.after_request
def add_ngrok_header(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# ===== favicon 避免無意義錯誤 =====
@app.route("/favicon.ico")
def favicon():
    return "", 204

# ===== 健康檢查 & 版本（部署驗證用）=====
@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.get("/version")
def version():
    return jsonify({
        "commit": os.getenv("RENDER_GIT_COMMIT", "local"),
        "env": "render"
    }), 200

# ===== Home =====
@app.route("/")
def home():
    return jsonify({"message": "Backend is running!"})

# ===== 公開 API：無需登入即可下單 =====
@app.route("/public_place_order", methods=["POST"])
def public_place_order():
    try:
        data = request.get_json(silent=True) or {}
        items = data.get("items", [])
        if not isinstance(items, list) or not items:
            return jsonify({"error": "items 欄位必須為非空陣列"}), 400

        order_ref = db.collection("users").document("dev011").collection("orders").document()
        order_ref.set({
            "items": items,
            "timestamp": firestore.SERVER_TIMESTAMP,
        })
        return jsonify({"message": "訂單已建立", "order_id": order_ref.id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===== 公開 API：無需登入即可取得菜單 =====
@app.route("/public_menus", methods=["GET"])
def public_menus():
    try:
        menus = []
        for doc in db.collection("menus").stream():
            item = doc.to_dict() or {}
            item["menu_id"] = doc.id
            menus.append(item)
        return jsonify({"menus": menus}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===== 主程式入口（本地執行；在 Render 由 gunicorn 啟動）=====
if __name__ == "__main__":
    with app.test_request_context():
        print("✅ Registered Routes:")
        print(app.url_map)

    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=DEBUG, use_reloader=False)

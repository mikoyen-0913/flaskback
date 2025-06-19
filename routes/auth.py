import hashlib
import jwt
import datetime
from flask import Blueprint, request, jsonify
from firebase_config import db
from functools import wraps

auth_bp = Blueprint('auth', __name__)
users_collection = "users"

# ✅ JWT 秘密金鑰與過期時間
JWT_SECRET = "your-secret-key"
JWT_EXPIRE_MINUTES = 60

# ✅ 密碼雜湊
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ✅ 建立 JWT Token
def generate_token(payload):
    payload["exp"] = datetime.datetime.utcnow() + datetime.timedelta(minutes=JWT_EXPIRE_MINUTES)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

# ✅ 驗證 Token 的裝飾器
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None

        # 從 header 抓取 token
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

        if not token:
            return jsonify({"error": "缺少 Token，請重新登入"}), 401

        try:
            decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            request.user = decoded
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token 已過期"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "無效的 Token"}), 401

        return f(*args, **kwargs)
    return decorated

# ✅ 註冊 API
@auth_bp.route('/signup', methods=['POST'])
def signup():
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        store_name = data.get('storeName', '').strip()
        email = data.get('email', '').strip()
        phone = data.get('phone', '').strip()
        address = data.get('address', '').strip()  # ✅ 新增地址欄位
        role = data.get('role') or 'staff'

        if not username: return jsonify({"error": "帳號不可為空"}), 400
        if not password: return jsonify({"error": "密碼不可為空"}), 400
        if not store_name: return jsonify({"error": "店名不可為空"}), 400
        if not email: return jsonify({"error": "Email 不可為空"}), 400
        if not phone: return jsonify({"error": "手機號碼不可為空"}), 400
        if not address: return jsonify({"error": "地址不可為空"}), 400  # ✅ 加入地址驗證

        # 檢查帳號是否已經存在
        existing_user = db.collection(users_collection).document(username).get()
        if existing_user.exists:
            return jsonify({"error": "帳號已存在"}), 409

        # 儲存資料
        user_data = {
            "username": username,
            "password_hash": hash_password(password),
            "store_name": store_name,
            "email": email,
            "phone": phone,
            "address": address,  # ✅ 加入地址
            "role": role
        }

        # 使用 username 作為 Document ID
        db.collection(users_collection).document(username).set(user_data)

        return jsonify({"message": "註冊成功"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ 登入 API
@auth_bp.route('/signin', methods=['POST'])
def signin():
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()

        if not username: return jsonify({"error": "請輸入帳號"}), 400
        if not password: return jsonify({"error": "請輸入密碼"}), 400

        # 使用 username 作為 Document ID 查詢
        user_doc = db.collection(users_collection).document(username).get()
        if not user_doc.exists:
            return jsonify({"error": "查無帳號，請重新輸入"}), 401

        user = user_doc.to_dict()
        if user["password_hash"] != hash_password(password):
            return jsonify({"error": "密碼錯誤"}), 401

        token = generate_token({
            "username": user["username"],
            "role": user["role"],  # 保持 role 欄位
            "store_name": user["store_name"]  # 新增 store_name 至 token
        })

        return jsonify({
            "message": "登入成功",
            "token": token,
            "username": user["username"],
            "role": user["role"],
            "store_name": user.get("store_name")
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

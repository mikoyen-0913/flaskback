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
        role = data.get('role') or 'staff'

        if not username: return jsonify({"error": "帳號不可為空"}), 400
        if not password: return jsonify({"error": "密碼不可為空"}), 400
        if not store_name: return jsonify({"error": "店名不可為空"}), 400
        if not email: return jsonify({"error": "Email 不可為空"}), 400
        if not phone: return jsonify({"error": "手機號碼不可為空"}), 400

        existing = db.collection(users_collection).where('username', '==', username).stream()
        if any(existing): return jsonify({"error": "帳號已存在"}), 409

        user_data = {
            "username": username,
            "password_hash": hash_password(password),
            "store_name": store_name,
            "email": email,
            "phone": phone,
            "role": role
        }

        db.collection(users_collection).add(user_data)
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

        password_hash = hash_password(password)
        query = db.collection(users_collection).where('username', '==', username).limit(1).stream()
        user_doc = next(query, None)
        if not user_doc:
            return jsonify({"error": "查無帳號，請重新輸入"}), 401

        user = user_doc.to_dict()
        if user["password_hash"] != password_hash:
            return jsonify({"error": "密碼錯誤"}), 401

        token = generate_token({
            "username": user["username"],
            "role": user["role"]
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

# ✅ 取得所有使用者資訊 API（需要 Token）
@auth_bp.route('/users', methods=['GET'])
@token_required
def get_all_users():
    try:
        users_ref = db.collection(users_collection).stream()
        users = []

        for doc in users_ref:
            user = doc.to_dict()
            user.pop("password_hash", None)  # 移除敏感欄位
            user["id"] = doc.id
            users.append(user)

        return jsonify({"users": users}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

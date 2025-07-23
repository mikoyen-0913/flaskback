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
JWT_EXPIRE_MINUTES = 720

# ✅ 密碼雜湊
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ✅ 建立 JWT Token
def generate_token(payload):
    payload["exp"] = datetime.datetime.utcnow() + datetime.timedelta(minutes=JWT_EXPIRE_MINUTES)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

# ✅ 驗證 Token 的裝飾器（支援 request.user）
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None

        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

        if not token:
            return jsonify({"error": "缺少 Token，請重新登入"}), 401

        try:
            decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            username = decoded.get("username")

            if not username:
                return jsonify({"error": "Token 無效：缺少 username"}), 401

            user_doc = db.collection(users_collection).document(username).get()
            if not user_doc.exists:
                return jsonify({"error": "找不到使用者"}), 404

            request.user = user_doc.to_dict()  # 附加到 request
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token 已過期"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "無效的 Token"}), 401
        except Exception as e:
            return jsonify({"error": str(e)}), 500

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
        address = data.get('address', '').strip()
        role = data.get('role') or 'staff'
        store_ids = data.get('store_ids', [])  # 對 superadmin 開放 store_ids

        if not username: return jsonify({"error": "帳號不可為空"}), 400
        if not password: return jsonify({"error": "密碼不可為空"}), 400
        if role != "superadmin" and not store_name:
            return jsonify({"error": "店名不可為空"}), 400
        if not email: return jsonify({"error": "Email 不可為空"}), 400
        if not phone: return jsonify({"error": "手機號碼不可為空"}), 400
        if not address: return jsonify({"error": "地址不可為空"}), 400

        existing_user = db.collection(users_collection).document(username).get()
        if existing_user.exists:
            return jsonify({"error": "帳號已存在"}), 409

        user_data = {
            "username": username,
            "password_hash": hash_password(password),
            "email": email,
            "phone": phone,
            "address": address,
            "role": role
        }

        if role == "superadmin":
            user_data["store_ids"] = store_ids  # 多間店授權
        else:
            user_data["store_name"] = store_name  # 一間店

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

        user_doc = db.collection(users_collection).document(username).get()
        if not user_doc.exists:
            return jsonify({"error": "查無帳號，請重新輸入"}), 401

        user = user_doc.to_dict()
        if user["password_hash"] != hash_password(password):
            return jsonify({"error": "密碼錯誤"}), 401

        role = user["role"]
        if role == "superadmin":
            store_ids = user.get("store_ids", [])
        else:
            store_ids = [user.get("store_name")]

        token = generate_token({
            "username": user["username"],
            "role": role,
            "store_ids": store_ids
        })

        return jsonify({
            "message": "登入成功",
            "token": token,
            "username": user["username"],
            "role": role,
            "store_ids": store_ids,
            "store_name": user.get("store_name", "")  # ✅ 補這裡
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

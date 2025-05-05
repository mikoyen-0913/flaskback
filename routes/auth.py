import hashlib
from flask import Blueprint, request, jsonify
from firebase_config import db

auth_bp = Blueprint('auth', __name__)
users_collection = "users"

# ✅ 密碼雜湊處理
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ✅ 註冊 API：/signup
@auth_bp.route('/signup', methods=['POST'])
def signup():
    try:
        data = request.get_json()

        # 讀取欄位
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        store_name = data.get('storeName', '').strip()
        email = data.get('email', '').strip()
        phone = data.get('phone', '').strip()
        role = data.get('role') or 'staff'  # ✅ role 可以為空，預設 staff

        # ✅ 檢查欄位是否為空（role 除外）
        if not username:
            return jsonify({"error": "帳號不可為空"}), 400
        if not password:
            return jsonify({"error": "密碼不可為空"}), 400
        if not store_name:
            return jsonify({"error": "店名不可為空"}), 400
        if not email:
            return jsonify({"error": "Email 不可為空"}), 400
        if not phone:
            return jsonify({"error": "手機號碼不可為空"}), 400

        # 檢查帳號是否存在
        existing = db.collection(users_collection).where('username', '==', username).stream()
        if any(existing):
            return jsonify({"error": "帳號已存在"}), 409

        # 建立使用者資料
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


# ✅ 登入 API：/signin
@auth_bp.route('/signin', methods=['POST'])
def signin():
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()

        # ✅ 檢查欄位是否為空
        if not username:
            return jsonify({"error": "請輸入帳號"}), 400
        if not password:
            return jsonify({"error": "請輸入密碼"}), 400

        password_hash = hash_password(password)

        # 🔍 查詢帳號是否存在
        query = db.collection(users_collection).where('username', '==', username).limit(1).stream()
        user_doc = next(query, None)

        if not user_doc:
            return jsonify({"error": "查無帳號，請重新輸入"}), 401

        user = user_doc.to_dict()

        # 🔒 密碼比對
        if user["password_hash"] != password_hash:
            return jsonify({"error": "密碼錯誤"}), 401

        return jsonify({
            "message": "登入成功",
            "username": user["username"],
            "role": user["role"],
            "store_name": user.get("store_name")
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

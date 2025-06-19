from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required

ingredients_bp = Blueprint('ingredients', __name__)
ingredients_collection = "ingredients"

# ✅ 取得所有食材（需登入）
@ingredients_bp.route('/get_ingredients', methods=['GET'])
@token_required
def get_ingredients():
    try:
        # 取得使用者的 store_name
        store_name = request.user.get("store_name")

        # 根據 store_name 動態選擇 Firestore 路徑
        ingredients_ref = db.collection("stores").document(store_name).collection("ingredients").stream()
        
        ingredients = [{"id": ing.id, **ing.to_dict()} for ing in ingredients_ref]
        return jsonify({"ingredients": ingredients}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
# ✅ 新增食材（需登入）
@ingredients_bp.route('/add_ingredient', methods=['POST'])
@token_required
def add_ingredient():
    try:
        # 檢查使用者角色是否為 developer（您可以根據需要修改為 'admin'）
        if request.user.get("role") != "developer":
            return jsonify({"error": "無權限新增食材"}), 403  # 只有 developer 才能新增食材

        data = request.get_json()

        # 打印接收到的資料，檢查是否正確傳送
        print("接收到的新增食材資料:", data)

        # 驗證必要欄位
        required_fields = ["name", "quantity", "unit", "price", "expiration_date"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"缺少必要欄位：{field}"}), 400

        # 驗證單位是否合法
        allowed_units = ["克", "毫升"]  # 只允許 "克" 和 "毫升"
        if data["unit"] not in allowed_units:
            return jsonify({"error": "無效的單位，請選擇 '克' 或 '毫升'"}), 400

        # 取得使用者的 store_name
        store_name = request.user.get("store_name")

        # 根據 store_name 動態選擇 Firestore 路徑
        # 直接執行 add 操作，並通過返回的 WriteResult 檢查成功寫入
        doc_ref = db.collection("stores").document(store_name).collection("ingredients").add({
            "name": data["name"],
            "quantity": data["quantity"],
            "unit": data["unit"],
            "price": data["price"],
            "expiration_date": data["expiration_date"]
        })

        # 使用返回的 DocumentReference 物件來確保寫入成功
        if doc_ref:
            # 返回新增的文檔ID
            return jsonify({"message": "食材新增成功", "doc_id": doc_ref.id})  # 正確獲取文檔ID
        else:
            return jsonify({"error": "食材新增失敗"}), 500

    except Exception as e:
        print("錯誤訊息:", str(e))  # 打印錯誤訊息
        return jsonify({"error": str(e)}), 500

# ✅ 更新食材（需登入）
@ingredients_bp.route('/update_ingredient/<ingredient_id>', methods=['PUT'])
@token_required
def update_ingredient(ingredient_id):
    try:
        data = request.get_json()

        # 可選擇更新欄位（後端不強制驗證所有欄位）
        updatable_fields = ["name", "quantity", "unit", "price", "expiration_date"]
        update_data = {key: data[key] for key in updatable_fields if key in data}

        if not update_data:
            return jsonify({"error": "沒有提供更新資料"}), 400

        # 取得使用者的 store_name
        store_name = request.user.get("store_name")

        # 根據 store_name 動態選擇 Firestore 路徑
        db.collection("stores").document(store_name).collection("ingredients").document(ingredient_id).update(update_data)
        
        return jsonify({"message": "食材更新成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 刪除食材（需登入）
@ingredients_bp.route('/delete_ingredient/<ingredient_id>', methods=['DELETE'])
@token_required
def delete_ingredient(ingredient_id):
    try:
        # 取得使用者的 store_name
        store_name = request.user.get("store_name")

        # 根據 store_name 動態選擇 Firestore 路徑
        db.collection("stores").document(store_name).collection("ingredients").document(ingredient_id).delete()

        return jsonify({"message": "食材刪除成功"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ 補貨功能（需登入）
@ingredients_bp.route('/restock_ingredients', methods=['POST'])
@token_required
def restock_ingredients():
    try:
        data = request.get_json()  # { "abc123": { "restock": 5 }, ... }

        if not isinstance(data, dict):
            return jsonify({"error": "資料格式錯誤"}), 400

        # 取得使用者的 store_name
        store_name = request.user.get("store_name")

        for ing_id, info in data.items():
            restock_amount = info.get("restock", 0)
            if not isinstance(restock_amount, (int, float)) or restock_amount <= 0:
                continue

            doc_ref = db.collection("stores").document(store_name).collection("ingredients").document(ing_id)
            doc = doc_ref.get()
            if not doc.exists:
                continue

            current_quantity = doc.to_dict().get("quantity", 0)
            new_quantity = current_quantity + restock_amount

            doc_ref.update({"quantity": new_quantity})

        return jsonify({"message": "補貨成功"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

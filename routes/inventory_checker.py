from flask import Blueprint, request, jsonify
from routes.auth import token_required
from tool.weather_from_graphql import fetch_weather_from_graphql
from tool.lstm_predict_all import forecast_next_sales, load_models_and_data
from tool.firebase_fetcher import fetch_ingredient_inventory, fetch_recipes
from tool.ingredient_demand import calculate_total_demand
import requests

# 註冊 Blueprint
inventory_bp = Blueprint('inventory', __name__)

# === 工具：將地址轉換為 GPS 座標（使用 OpenStreetMap Nominatim）===
def geocode_address(address):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "tw",          # ✅ 限定查詢範圍在台灣
        "accept-language": "zh-TW"     # ✅ 回傳繁體中文地名
    }

    try:
        response = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        data = response.json()

        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            display_name = data[0].get("display_name", "未知地點")
            print(f"📍 地址轉換成功：{address} → 緯度 {lat}, 經度 {lon}（{display_name}）")
            return lat, lon
        else:
            print("❌ 查無地址資料，改用預設位置（台中中區）")
            return 24.15, 120.65

    except Exception as e:
        print(f"❌ 地址轉換失敗：{e}，使用預設位置（台中中區）")
        return 24.15, 120.65


# === API 路由：自動根據使用者帳號計算店鋪缺料狀況 ===
@inventory_bp.route('/check_inventory', methods=['POST'])
@token_required
def check_inventory():
    print("🔍 使用者資訊：", request.user)
    try:
        # 🔐 從登入者資訊取得 address 與 store_name
        address = request.user.get("address")
        store_name = request.user.get("store_name")

        if not address:
            return jsonify({"error": "使用者未設定地址"}), 400
        if not store_name:
            return jsonify({"error": "使用者未設定店家 store_name"}), 400

        # 1️⃣ 地址 → GPS
        lat, lon = geocode_address(address)

        # 2️⃣ 取得未來天氣
        api_key = "CWA-6FAB80A3-75EF-4555-86C3-A026F8F0E564"
        forecast_data = fetch_weather_from_graphql(lat, lon, api_key)
        if not forecast_data:
            return jsonify({"error": "氣象資料取得失敗"}), 400

        # 3️⃣ 載入模型與口味名稱
        models, scalers, pivot_df, flavors = load_models_and_data()

        # 4️⃣ 預測銷量
        predicted_sales = {}
        for flavor in flavors:
            model = models.get(flavor)
            scaler = scalers.get(flavor)
            if model and scaler:
                y_pred = forecast_next_sales(flavor, pivot_df, model, scaler, forecast_data)
                predicted_sales[flavor] = sum(y_pred)

        # 5️⃣ 根據使用者店名抓食材庫存與配方需求
        inventory = fetch_ingredient_inventory(store_name)
        recipes = fetch_recipes()
        demand = calculate_total_demand(predicted_sales, recipes)

        # 6️⃣ 缺料報告
        shortage_report = {}
        for ingredient, info in demand.items():
            required = info["total"]
            unit = info["unit"]
            ingredient_name = ingredient.upper()

            current = inventory.get(ingredient_name, {"quantity": 0, "unit": unit})
            available = current["quantity"]
            available_unit = current["unit"]

            if unit != available_unit:
                shortage_report[ingredient_name] = {
                    "status": "單位不一致",
                    "required": required,
                    "available": available
                }
            elif required > available:
                shortage_report[ingredient_name] = {
                    "status": "缺料",
                    "required": required,
                    "available": available,
                    "shortage": required - available
                }
            else:
                shortage_report[ingredient_name] = {
                    "status": "足夠",
                    "required": required,
                    "available": available
                }

        # 7️⃣ 結果回傳
        return jsonify({
            "forecasted_demand": demand,
            "inventory": inventory,
            "shortage_report": shortage_report
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

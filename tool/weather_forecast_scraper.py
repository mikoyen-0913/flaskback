# weather_forecast_scraper.py（使用 GraphQL + GPS 查天氣）
from weather_from_graphql import fetch_weather_from_graphql
from lstm_predict_all import forecast_next_sales, load_models_and_data

if __name__ == "__main__":
    print("📍 請輸入你的 GPS 位置（查天氣 → 預測銷量）：")
    lat = float(input("緯度 (latitude)："))
    lon = float(input("經度 (longitude)："))

    api_key = "CWA-6FAB80A3-75EF-4555-86C3-A026F8F0E564"
    forecast_data = fetch_weather_from_graphql(lat, lon, api_key)

    if not forecast_data:
        print("❌ 無法取得氣象資料，結束預測。")
    else:
        models, scalers, pivot_df, flavors = load_models_and_data()

        print("\n📊 所有口味未來三天銷量預測：")
        for flavor in flavors:
            model = models.get(flavor)
            scaler = scalers.get(flavor)

            if model is None or scaler is None:
                print(f"⚠️ {flavor} 模型缺失，略過")
                continue

            y_pred = forecast_next_sales(
                flavor=flavor,
                pivot_df=pivot_df,
                model=model,
                scaler=scaler,
                future_weather=forecast_data
            )

            print(f"\n🍪 {flavor}:")
            for i, value in enumerate(y_pred, 1):
                print(f"  第 {i} 天：{value} 顆")
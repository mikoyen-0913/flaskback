# lstm_predict_all.py
import os
import pandas as pd
import numpy as np
import joblib
from tensorflow.keras.models import load_model
from datetime import timedelta

MODEL_DIR = "models"
SEQ_LEN = 7
PRED_DAYS = 3

# === 載入資料 ===
df = pd.read_excel("adjusted_projectdata_v5_limited.xlsx")
df['sales'] = df['adjusted_sales_v5']
df['date'] = pd.to_datetime(df['date'])
df['weekday'] = df['date'].dt.weekday
df = df.sort_values(by=['flavor', 'date'])

pivot_df = df.pivot_table(index='date', columns='flavor', values=['sales', 'rainfall', 'temperature', 'weekday'])
pivot_df = pivot_df.fillna(0)
pivot_df.columns = ['{}_{}'.format(var, flavor) for var, flavor in pivot_df.columns]

flavors = df['flavor'].unique()

# === 載入模型與 scaler ===
def load_models_and_data():
    models = {}
    scalers = {}
    for flavor in flavors:
        model_path = f"{MODEL_DIR}/{flavor}_model.h5"
        scaler_path = f"{MODEL_DIR}/{flavor}_scaler.pkl"
        if os.path.exists(model_path) and os.path.exists(scaler_path):
            models[flavor] = load_model(model_path, compile=False)
            scalers[flavor] = joblib.load(scaler_path)
    return models, scalers, pivot_df, flavors

# === 預測函式 ===
def forecast_next_sales(flavor, pivot_df, model, scaler, future_weather, seq_len=7, pred_days=3):
    features = [f'rainfall_{flavor}', f'temperature_{flavor}', f'weekday_{flavor}']
    target = f'sales_{flavor}'
    data = pivot_df[[*features, target]].copy()
    latest_known = data[-seq_len:].copy()

    last_known_date = pivot_df.index[-1]
    for i, day in enumerate(future_weather):
        future_date = last_known_date + timedelta(days=i + 1)
        weekday = future_date.weekday()
        latest_known.loc[len(latest_known)] = [day['rainfall'], day['temperature'], weekday, 0]

    full_scaled = scaler.transform(latest_known)
    x_input = full_scaled[:seq_len, :-1].reshape(1, seq_len, -1)

    y_pred_scaled = model.predict(x_input, verbose=0)
    dummy = np.zeros((pred_days, data.shape[1]))
    dummy[:, data.columns.get_loc(target)] = y_pred_scaled.flatten()
    y_pred_inv = scaler.inverse_transform(dummy)[:, data.columns.get_loc(target)]

    return y_pred_inv.round(1)

# ✅ CLI 測試入口
if __name__ == '__main__':
    models, scalers, pivot_df, flavors = load_models_and_data()

    while True:
        do_predict = input("\n👉 是否要預測所有口味未來 3 天銷量？(y/n): ").strip().lower()
        if do_predict != 'y':
            break

        future_weather = []
        print("請輸入未來三天的『雨量』與『氣溫』：")
        for i in range(1, 4):
            rain = float(input(f"  第 {i} 天雨量（mm）："))
            temp = float(input(f"  第 {i} 天氣溫（℃）："))
            future_weather.append({'rainfall': rain, 'temperature': temp})

        print("\n📊 所有口味預測結果：")
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
                future_weather=future_weather
            )

            print(f"\n🍪 {flavor}:")
            for i, value in enumerate(y_pred, 1):
                print(f"  第 {i} 天：{value} 顆")

# train_and_save_models.py
import os
import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
import joblib

# === 參數與資料路徑 ===
MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)

SEQ_LEN = 7
PRED_DAYS = 3

# === 資料預處理 ===
df = pd.read_excel('adjusted_projectdata_v5_limited.xlsx')
df['sales'] = df['adjusted_sales_v5']
df['date'] = pd.to_datetime(df['date'])
df['weekday'] = df['date'].dt.weekday
df = df.sort_values(by=['flavor', 'date'])

pivot_df = df.pivot_table(index='date', columns='flavor', values=['sales', 'rainfall', 'temperature', 'weekday'])
pivot_df = pivot_df.fillna(0)
pivot_df.columns = ['{}_{}'.format(var, flavor) for var, flavor in pivot_df.columns]

# === 時序切片 ===
def create_sequences(data, seq_len=7, predict_days=3):
    X, y = [], []
    for i in range(len(data) - seq_len - predict_days + 1):
        X.append(data[i:i+seq_len, :-1])
        y.append(data[i+seq_len:i+seq_len+predict_days, -1])
    return np.array(X), np.array(y)

# === 模型架構 ===
def build_model(input_shape, pred_days):
    model = Sequential()
    model.add(LSTM(128, return_sequences=True, activation='relu', input_shape=input_shape))
    model.add(Dropout(0.2))
    model.add(LSTM(64, activation='relu'))
    model.add(Dropout(0.2))
    model.add(Dense(pred_days))
    model.compile(optimizer='adam', loss='mse')
    return model

# === 訓練與儲存 ===
flavors = df['flavor'].unique()

for flavor in flavors:
    print(f"\n🔍 訓練並儲存模型：{flavor}")

    features = [f'rainfall_{flavor}', f'temperature_{flavor}', f'weekday_{flavor}']
    target = f'sales_{flavor}'
    if not all(col in pivot_df.columns for col in [*features, target]):
        print(f"⚠️ 缺欄位，跳過 {flavor}")
        continue

    data = pivot_df[[*features, target]].copy()
    scaler = MinMaxScaler()
    scaled_data = scaler.fit_transform(data)

    X, y = create_sequences(scaled_data, SEQ_LEN, PRED_DAYS)
    if len(X) == 0:
        print(f"⚠️ 資料不足，跳過 {flavor}")
        continue

    model = build_model((SEQ_LEN, X.shape[2]), PRED_DAYS)
    model.fit(X, y, epochs=100, batch_size=8, validation_split=0.1, verbose=0)

    # === 儲存模型與 scaler ===
    model.save(f"{MODEL_DIR}/{flavor}_model.h5")
    joblib.dump(scaler, f"{MODEL_DIR}/{flavor}_scaler.pkl")

print("✅ 所有模型與 scaler 已儲存完畢。")

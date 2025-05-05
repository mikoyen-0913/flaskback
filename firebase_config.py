import os
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# ✅ 載入 .env 環境變數
load_dotenv()

# ✅ 取得金鑰路徑（預設為 .env 中指定的 FIREBASE_CREDENTIAL_PATH）
firebase_key_path = os.getenv("FIREBASE_CREDENTIAL_PATH")
print("🔍 Firebase 金鑰存在？", os.path.exists(firebase_key_path))

# ✅ 初始化 Firebase Admin SDK
cred = credentials.Certificate(firebase_key_path)
firebase_admin.initialize_app(cred)

# ✅ 建立 Firestore 實例
db = firestore.client()


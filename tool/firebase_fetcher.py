import firebase_admin
from firebase_admin import credentials, firestore

db = None

def init_firebase():
    global db

    # ✅ 若已初始化過就跳過
    if db is not None:
        print("⚠️ Firebase 已初始化，直接使用")
        return

    try:
        # ✅ firebase_admin._apps 檢查是否已呼叫 initialize_app()
        if not firebase_admin._apps:
            cred = credentials.Certificate("yaoyaoproject-88907-firebase-adminsdk-fbsvc-b498692948.json")
            firebase_admin.initialize_app(cred)
            print("✅ Firebase 初始化成功")
        else:
            print("⚠️ Firebase app 已存在")

        db = firestore.client()
    except Exception as e:
        print(f"❌ Firebase 初始化失敗: {e}")
        exit()

# ✅ 根據店別名稱讀取該店食材庫存
def fetch_ingredient_inventory(store_name):
    init_firebase()
    if db is None:
        print("❌ Firestore 客戶端尚未初始化")
        exit()

    ingredients_ref = db.collection("stores").document(store_name).collection("ingredients")
    inventory = {}
    for doc in ingredients_ref.stream():
        data = doc.to_dict()
        name = data.get("name")
        quantity = data.get("quantity", 0)
        unit = data.get("unit", "")
        if name:  # 防止空白名稱
            inventory[name.upper()] = {"quantity": quantity, "unit": unit}
    return inventory

# ✅ 讀取食譜資料
def fetch_recipes():
    init_firebase()
    if db is None:
        print("❌ Firestore 客戶端尚未初始化")
        exit()

    recipes_ref = db.collection("recipes")
    recipes = {}
    for doc in recipes_ref.stream():
        data = doc.to_dict()
        flavor_name = doc.id
        recipes[flavor_name] = {}
        for ingredient, detail in data.items():
            amount = detail.get("amount")
            unit = detail.get("unit")
            if amount is not None and unit is not None:
                recipes[flavor_name][ingredient] = (amount, unit)
    return recipes

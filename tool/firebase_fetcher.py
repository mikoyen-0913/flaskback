# tool/firebase_fetcher.py
import firebase_admin
from firebase_admin import credentials, firestore
from typing import Dict, Any, List, Tuple

db = None

# ====== 初始化 ======
def init_firebase():
    global db

    # 已初始化就跳過
    if db is not None:
        print("⚠️ Firebase 已初始化，直接使用")
        return

    try:
        # 尚未 initialize_app 才做
        if not firebase_admin._apps:
            cred = credentials.Certificate("yaoyaoproject-88907-firebase-adminsdk-fbsvc-b498692948.json")
            firebase_admin.initialize_app(cred)
            print("✅ Firebase 初始化成功")
        else:
            print("⚠️ Firebase app 已存在")

        db = firestore.client()
    except Exception as e:
        print(f"❌ Firebase 初始化失敗: {e}")
        raise


# ====== 共同：效期欄位鍵名（中英並存） ======
DATE_KEYS: Tuple[str, ...] = (
    # 英文常見
    "expiration_date", "expiry_date", "expire_date", "expire_on",
    "best_before", "best_before_date", "valid_until", "date",
    "expiration", "expirationDate", "validUntil", "exp", "expiry",
    "shelf_life",
    # 中文常見
    "保存期限", "到期日", "效期", "效期日", "效期至", "保存期限日", "保質期",
)


def _pick_dates(d: Dict[str, Any]) -> Dict[str, Any]:
    """從 dict 擷取所有存在的效期欄位（不轉型，原樣帶出）。"""
    out: Dict[str, Any] = {}
    for k in DATE_KEYS:
        if k in d and d[k] not in (None, ""):
            out[k] = d[k]
    return out


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, (int, float)):
            return float(x)
        return float(str(x).strip())
    except Exception:
        return default


# ====== 根據店別名稱讀取該店食材庫存（含效期與批次） ======
def fetch_ingredient_inventory(store_name: str) -> Dict[str, Any]:
    """
    回傳格式（兩種情形擇一，取決於你 Firestore 的資料型態）：
    1) 單層文件（無 batches 子集合）
       {
         "泡打粉": { "quantity": 515.92, "unit": "克", "expiration_date": "2025-07-21" },
         "牛奶":   { "quantity": 25583.8, "unit": "毫升", "到期日": "2025-09-06" },
         ...
       }

    2) 有批次的文件（有 batches 子集合）
       {
         "牛奶": [
           {"quantity": 1000, "unit": "毫升", "expiration_date": "2025-09-04"},
           {"quantity":  800, "unit": "毫升", "expiration_date": "2025-09-06"}
         ],
         ...
       }
    """
    init_firebase()
    if db is None:
        raise RuntimeError("❌ Firestore 客戶端尚未初始化")

    ingredients_ref = db.collection("stores").document(store_name).collection("ingredients")
    inventory: Dict[str, Any] = {}

    for doc in ingredients_ref.stream():
        data = doc.to_dict() or {}
        # 名稱優先使用欄位 name，否則用 document id
        name = (data.get("name") or doc.id).strip().upper()
        if not name:
            continue  # 防止空白名稱

        # 先嘗試讀取子集合 batches（若存在則使用批次模式）
        batches_ref = ingredients_ref.document(doc.id).collection("batches")
        try:
            batch_docs = list(batches_ref.stream())
        except Exception:
            batch_docs = []

        if batch_docs:
            batch_list: List[Dict[str, Any]] = []
            for b in batch_docs:
                bd = b.to_dict() or {}
                item: Dict[str, Any] = {
                    "quantity": _to_float(bd.get("quantity"), 0.0),
                }
                if "unit" in bd:
                    item["unit"] = bd["unit"]
                # 帶出所有可能的日期欄位
                item.update(_pick_dates(bd))
                batch_list.append(item)

            inventory[name] = batch_list
            continue

        # 沒有批次就使用單層文件
        entry: Dict[str, Any] = {
            "quantity": _to_float(data.get("quantity"), 0.0),
            "unit": data.get("unit", ""),
        }
        entry.update(_pick_dates(data))
        inventory[name] = entry

    # 方便除錯（可暫時打開）
    # print("[fetch_ingredient_inventory] sample:", list(inventory.items())[:3])
    return inventory


# ====== 讀取食譜資料 ======
def fetch_recipes() -> Dict[str, Dict[str, Tuple[float, str]]]:
    """
    回傳：
    {
      "珍珠鮮奶油": {
        "雞蛋": (100, "克"),
        "牛奶": (200, "毫升"),
        ...
      },
      ...
    }
    """
    init_firebase()
    if db is None:
        raise RuntimeError("❌ Firestore 客戶端尚未初始化")

    recipes_ref = db.collection("recipes")
    recipes: Dict[str, Dict[str, Tuple[float, str]]] = {}

    for doc in recipes_ref.stream():
        data = doc.to_dict() or {}
        flavor_name = doc.id
        recipes[flavor_name] = {}

        # 允許兩種結構：
        # A) { "雞蛋": {"amount": 100, "unit": "克"}, "牛奶": {"amount": 200, "unit": "毫升"} }
        # B) { "ingredients": { "雞蛋": {"amount": 100, "unit": "克"}, ... } }
        ingredients_obj = data.get("ingredients") if isinstance(data.get("ingredients"), dict) else data

        for ingredient, detail in (ingredients_obj or {}).items():
            # detail 可能是 dict，也可能直接是數字
            amount = None
            unit = None
            if isinstance(detail, dict):
                amount = _to_float(detail.get("amount"))
                unit = detail.get("unit")
            else:
                # 若是直接數值，則需要有個預設單位（這裡若沒有就略過）
                amount = _to_float(detail)
                unit = None

            if amount is not None and unit:
                recipes[flavor_name][ingredient] = (amount, unit)

    return recipes

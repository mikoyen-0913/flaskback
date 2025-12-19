# tool/firebase_fetcher.py
import firebase_admin
from firebase_admin import credentials, firestore
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, date, timezone

db = None

# ====== 初始化 ======
def init_firebase():
    global db

    # 已初始化就跳過
    if db is not None:
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

def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, (int, float)):
            return float(x)
        return float(str(x).strip())
    except Exception:
        return default

def _parse_to_date(raw: Any) -> Optional[date]:
    """相容 datetime/date/字串/epoch/Firestore Timestamp(dict)."""
    if raw is None:
        return None

    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()

    if isinstance(raw, dict):
        sec = raw.get("_seconds") or raw.get("seconds")
        if sec is not None:
            if sec > 1e12:
                sec = sec / 1000.0
            return datetime.fromtimestamp(float(sec), tz=timezone.utc).date()
        if "value" in raw:
            return _parse_to_date(raw["value"])

    if isinstance(raw, (int, float)):
        sec = raw if raw <= 1e12 else raw / 1000.0
        return datetime.fromtimestamp(float(sec), tz=timezone.utc).date()

    if isinstance(raw, str):
        s = raw.strip().replace("/", "-")
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(s).date()
            except Exception:
                return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    return None

def _pick_any_expiration(d: Dict[str, Any]) -> Optional[date]:
    """從父文件抓任意可解析的效期欄位（中英都支援）。"""
    for k in DATE_KEYS:
        if k in d and d[k] not in (None, ""):
            dt = _parse_to_date(d[k])
            if dt:
                return dt
    return None


# ==========================================================
# ✅ 讀取店內庫存（批次總量版）
# ==========================================================
def fetch_ingredient_inventory(store_name: str) -> Dict[str, Dict[str, Any]]:
    """
    ✅ 固定回傳『單層 dict』，每個食材一筆：

    {
      "牛奶": {"quantity": 1800, "unit": "毫升", "expiration_date": "2025-09-04"},
      "泡打粉": {"quantity": 515.92, "unit": "克", "expiration_date": "2025-07-21"},
      ...
    }

    計算規則（你現在的批次庫存設計）：
    - 可用庫存 = sum(batches where status in ["in_use", "unused"])
      （忽略 depleted）
    - expiration_date = 在上述可用批次中，挑最早的 expiration_date（若有）
    - 若某食材沒有 batches（舊資料），fallback 用父文件 quantity/expiration_date
    - key：一律用『食材名稱大寫』，方便跟食譜需求對齊
    """
    init_firebase()
    if db is None:
        raise RuntimeError("❌ Firestore 客戶端尚未初始化")

    ingredients_ref = db.collection("stores").document(store_name).collection("ingredients")
    inventory: Dict[str, Dict[str, Any]] = {}

    for doc in ingredients_ref.stream():
        data = doc.to_dict() or {}

        name = (data.get("name") or doc.id or "").strip()
        if not name:
            continue

        key = name.upper()
        unit = data.get("unit", "")

        batches_ref = ingredients_ref.document(doc.id).collection("batches")

        total = 0.0
        earliest: Optional[date] = None
        has_any_batch = False

        try:
            # 用兩次 where 最穩（避免 in() 在不同環境的兼容問題）
            for st in ("in_use", "unused"):
                for b in batches_ref.where("status", "==", st).stream():
                    has_any_batch = True
                    bd = b.to_dict() or {}
                    qty = _to_float(bd.get("quantity"), 0.0)
                    if qty > 0:
                        total += qty

                    exp = _parse_to_date(bd.get("expiration_date"))
                    if exp:
                        earliest = exp if (earliest is None or exp < earliest) else earliest
        except Exception:
            # 批次讀取失敗：退回父文件
            has_any_batch = False

        if not has_any_batch:
            total = _to_float(data.get("quantity"), 0.0)
            exp_parent = _pick_any_expiration(data)
            earliest = exp_parent if exp_parent else None

        entry: Dict[str, Any] = {
            "quantity": float(total),
            "unit": unit,
        }
        if earliest:
            entry["expiration_date"] = earliest.isoformat()

        inventory[key] = entry

    return inventory


# ==========================================================
# 讀取食譜資料
# ==========================================================
def fetch_recipes() -> Dict[str, Dict[str, Tuple[float, str]]]:
    """
    回傳：
      {
        "奶油紅豆餅": {"糖": (10, "克"), "牛奶": (20, "毫升")},
        ...
      }
    """
    init_firebase()
    if db is None:
        raise RuntimeError("❌ Firestore 客戶端尚未初始化")

    recipes_ref = db.collection("recipes")
    recipes: Dict[str, Dict[str, Tuple[float, str]]] = {}

    for doc in recipes_ref.stream():
        flavor_name = doc.id
        data = doc.to_dict() or {}

        recipes[flavor_name] = {}

        for ingredient, detail in data.items():
            amount = None
            unit = None
            if isinstance(detail, dict):
                amount = _to_float(detail.get("amount"))
                unit = detail.get("unit")
            else:
                amount = _to_float(detail)
                unit = None

            if amount is not None and unit:
                recipes[flavor_name][ingredient] = (amount, unit)

    return recipes

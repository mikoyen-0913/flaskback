from flask import Blueprint, request, jsonify
from firebase_config import db
from routes.auth import token_required
from google.cloud import firestore
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import os
from typing import Any, Dict, Optional, List, Tuple

# 設定時區
try:
    TZ = ZoneInfo("Asia/Taipei")
except Exception:
    import pytz
    TZ = pytz.timezone("Asia/Taipei")

ingredients_bp = Blueprint("ingredients", __name__)

# ==========================
# 共用：單位/日期工具
# ==========================
UNIT_ALIAS = {"g": "克", "kg": "克", "ml": "毫升", "l": "毫升", "公克": "克", "公升": "毫升"}
MULTIPLIER = {("kg", "克"): 1000, ("l", "毫升"): 1000}


def normalize_unit(unit: Any) -> str:
    s = str(unit or "").strip()
    if not s:
        return s
    return UNIT_ALIAS.get(s.lower(), s)


def convert_amount(ingredient_unit: str, recipe_unit: str, amount: float) -> float:
    """把 recipe 的用量 amount 轉成 ingredient 文件的單位"""
    key = (ingredient_unit, recipe_unit)
    if key in MULTIPLIER:
        return amount / MULTIPLIER[key]
    elif (recipe_unit, ingredient_unit) in MULTIPLIER:
        return amount * MULTIPLIER[(recipe_unit, ingredient_unit)]
    return amount


def taipei_today_str(dt_utc: Optional[datetime] = None) -> str:
    if dt_utc is None:
        dt_utc = datetime.utcnow()
    return (dt_utc + timedelta(hours=8)).strftime("%Y%m%d")


def _parse_exp_date(val: Any) -> Optional[date]:
    """把 expiration_date 轉成 date 方便比較（支援 string / datetime / firestore timestamp dict）"""
    if val is None:
        return None

    if isinstance(val, date) and not isinstance(val, datetime):
        return val

    if isinstance(val, datetime):
        return val.date()

    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            try:
                return datetime.strptime(s[:10], "%Y/%m/%d").date()
            except Exception:
                return None

    if isinstance(val, dict):
        sec = val.get("seconds") or val.get("_seconds")
        if sec is not None:
            try:
                return datetime.utcfromtimestamp(int(sec)).date()
            except Exception:
                return None

    return None


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _to_int(v: Any) -> int:
    try:
        return int(round(_safe_float(v, 0.0)))
    except Exception:
        return 0


def _sum_batches_and_earliest(ing_ref) -> Tuple[float, Optional[date], float, Optional[date], bool]:
    """
    回傳：
      (in_use_sum, in_use_earliest, unused_sum, unused_earliest, has_any_batches)
    """
    in_use_sum = 0.0
    unused_sum = 0.0
    in_use_earliest = None
    unused_earliest = None
    has_any = False

    try:
        docs = ing_ref.collection("batches").where("status", "in", ["in_use", "unused"]).stream()
        for b in docs:
            has_any = True
            bd = b.to_dict() or {}
            status = bd.get("status")
            qty = _safe_float(bd.get("quantity", 0), 0.0)
            exp = _parse_exp_date(bd.get("expiration_date"))

            if status == "in_use":
                in_use_sum += qty
                if exp and (in_use_earliest is None or exp < in_use_earliest):
                    in_use_earliest = exp
            elif status == "unused":
                unused_sum += qty
                if exp and (unused_earliest is None or exp < unused_earliest):
                    unused_earliest = exp
    except Exception as e:
        print("read batches error:", e)

    return in_use_sum, in_use_earliest, unused_sum, unused_earliest, has_any


def _ensure_in_use_batch(ing_ref, unit: Optional[str], price: Optional[float], qty: float, exp_date: Optional[str]) -> str:
    """
    確保該食材至少有一個 in_use 批次：
    - 若有 current_batch_id 且該批存在 → 更新它
    - 否則建立新的 in_use 批次並回傳 batch_id
    """
    ing_snap = ing_ref.get()
    ing = ing_snap.to_dict() or {}
    current_batch_id = ing.get("current_batch_id")

    batches_col = ing_ref.collection("batches")

    if current_batch_id:
        b_ref = batches_col.document(current_batch_id)
        b_snap = b_ref.get()
        if b_snap.exists:
            upd = {"status": "in_use"}
            if qty is not None:
                upd["quantity"] = float(qty)
            if unit:
                upd["unit"] = unit
            if exp_date is not None:
                upd["expiration_date"] = exp_date
            if price is not None:
                upd["price"] = float(price)
            b_ref.update(upd)
            return current_batch_id

    # 沒 current 或找不到 → 新建
    b_ref = batches_col.document()
    b_ref.set({
        "created_at": firestore.SERVER_TIMESTAMP,
        "status": "in_use",
        "quantity": float(qty),
        "original_quantity": float(qty),
        "unit": unit or ing.get("unit"),
        "expiration_date": exp_date,
        "price": float(price) if price is not None else float(ing.get("price", 0) or 0),
        "note": "init/edited as in_use",
    })
    return b_ref.id


# ==========================
# ✅ 店長端：庫存列表（使用中 + 預備庫存）
# ==========================
@ingredients_bp.route("/get_ingredients", methods=["GET"])
@token_required
def get_ingredients():
    """
    店長端庫存列表：
    - 使用中庫存：sum(batches.status == in_use)
    - 預備庫存：sum(batches.status == unused)
    - 效期：各自取最早 expiration_date
    輸出皆為整數（你前端要整數）
    """
    try:
        store_name = request.user.get("store_name")
        base_col = db.collection("stores").document(store_name).collection("ingredients")
        ing_snaps = list(base_col.stream())

        ingredients = []
        for ing in ing_snaps:
            data = ing.to_dict() or {}
            ing_ref = base_col.document(ing.id)

            in_use_sum, in_use_earliest, unused_sum, unused_earliest, has_batches = _sum_batches_and_earliest(ing_ref)

            # 以批次為準；若完全沒 batches（舊資料）才 fallback 父文件
            if has_batches:
                current_qty_int = int(round(in_use_sum))
                data["current_quantity"] = current_qty_int
                data["quantity"] = current_qty_int
                data["expiration_date"] = in_use_earliest.isoformat() if in_use_earliest else None
            else:
                raw = data.get("current_quantity", data.get("quantity", 0))
                current_qty_int = _to_int(raw)
                data["current_quantity"] = current_qty_int
                data["quantity"] = current_qty_int
                # expiration_date 保留父文件（可能是舊資料）

            # 預備庫存欄位
            data["reserved_quantity"] = int(round(unused_sum))
            data["reserved_expiration_date"] = unused_earliest.isoformat() if unused_earliest else None

            # 父文件 status 給前端參考（沒有也不強求）
            if "status" not in data:
                data["status"] = "in_stock" if current_qty_int > 0 else "out_of_stock"

            ingredients.append({"id": ing.id, **data})

        return jsonify({"ingredients": ingredients}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# ✅ 新增食材：建立父文件 + 建立 in_use 初始批次（讓「新增」不再是舊世界）
# ==========================
@ingredients_bp.route("/add_ingredient", methods=["POST"])
@token_required
def add_ingredient():
    try:
        if request.user.get("role") != "developer":
            return jsonify({"error": "無權限新增食材"}), 403

        data = request.get_json() or {}
        required_fields = ["name", "quantity", "unit", "expiration_date", "price"]
        for f in required_fields:
            if f not in data:
                return jsonify({"error": f"缺少欄位: {f}"}), 400

        store_name = request.user.get("store_name")
        ing_ref = db.collection("stores").document(store_name).collection("ingredients").document()

        name = str(data["name"]).strip()
        unit = normalize_unit(data["unit"])
        price = float(data["price"])
        qty = float(data["quantity"])
        exp_date = data.get("expiration_date")

        # 先建父文件（父文件只當「快速顯示」；真正以 batches 為準）
        ing_ref.set({
            "name": name,
            "unit": unit,
            "price": price,
            "status": "in_stock" if qty > 0 else "out_of_stock",
            "created_at": firestore.SERVER_TIMESTAMP,
            "current_batch_id": None,
            "current_quantity": int(round(max(qty, 0))),
            "quantity": int(round(max(qty, 0))),
        })

        # 建立 in_use 初始批次（qty<=0 就不建）
        if qty > 0:
            b_ref = ing_ref.collection("batches").document()
            b_ref.set({
                "created_at": firestore.SERVER_TIMESTAMP,
                "status": "in_use",
                "quantity": float(qty),
                "original_quantity": float(qty),
                "unit": unit,
                "expiration_date": exp_date,
                "price": float(price),
                "note": "init batch (in_use) from add_ingredient",
            })
            ing_ref.update({
                "current_batch_id": b_ref.id,
                "current_quantity": int(round(qty)),
                "quantity": int(round(qty)),
                "expiration_date": exp_date,  # 父文件顯示用
                "status": "in_stock",
            })

        return jsonify({"message": "新增成功"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# ✅ 編輯食材：更新父文件 meta；若有 quantity/expiration_date 則更新 in_use 批次
# ==========================
@ingredients_bp.route("/update_ingredient/<ingredient_id>", methods=["PUT"])
@token_required
def update_ingredient(ingredient_id):
    try:
        if request.user.get("role") != "developer":
            return jsonify({"error": "無權限更新食材"}), 403

        store_name = request.user.get("store_name")
        ing_ref = db.collection("stores").document(store_name).collection("ingredients").document(ingredient_id)

        snap = ing_ref.get()
        if not snap.exists:
            return jsonify({"error": "找不到食材"}), 404

        payload = request.get_json() or {}

        # 先更新父文件（名稱/單位/價格）
        upd_parent: Dict[str, Any] = {}
        if "name" in payload:
            upd_parent["name"] = str(payload["name"]).strip()
        if "unit" in payload:
            upd_parent["unit"] = normalize_unit(payload["unit"])
        if "price" in payload:
            upd_parent["price"] = float(payload["price"])

        # 若帶 quantity/expiration_date：代表你前端「編輯」其實想改使用中庫存那一批
        touch_qty = "quantity" in payload or "current_quantity" in payload
        qty_val = payload.get("quantity", payload.get("current_quantity"))
        exp_date = payload.get("expiration_date") if "expiration_date" in payload else None

        if touch_qty:
            qty = float(qty_val) if qty_val is not None else 0.0
            unit = upd_parent.get("unit") or normalize_unit((snap.to_dict() or {}).get("unit"))
            price = upd_parent.get("price")
            # 確保有 in_use 批次可改
            batch_id = _ensure_in_use_batch(ing_ref, unit=unit, price=price, qty=qty, exp_date=exp_date)

            upd_parent["current_batch_id"] = batch_id
            upd_parent["current_quantity"] = int(round(max(qty, 0)))
            upd_parent["quantity"] = int(round(max(qty, 0)))
            if exp_date is not None:
                upd_parent["expiration_date"] = exp_date
            upd_parent["status"] = "in_stock" if qty > 0 else "out_of_stock"

        if upd_parent:
            ing_ref.update(upd_parent)

        return jsonify({"message": "更新成功"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# ✅ 刪除食材：連同 batches 子集合一起刪（避免殘留）
# ==========================
@ingredients_bp.route("/delete_ingredient/<ingredient_id>", methods=["DELETE"])
@token_required
def delete_ingredient(ingredient_id):
    try:
        if request.user.get("role") != "developer":
            return jsonify({"error": "無權限刪除食材"}), 403

        store_name = request.user.get("store_name")
        ing_ref = db.collection("stores").document(store_name).collection("ingredients").document(ingredient_id)

        snap = ing_ref.get()
        if not snap.exists:
            return jsonify({"error": "找不到食材"}), 404

        # 先刪 batches
        batches = list(ing_ref.collection("batches").stream())
        if batches:
            batch_writer = db.batch()
            for b in batches:
                batch_writer.delete(b.reference)
            batch_writer.commit()

        ing_ref.delete()
        return jsonify({"message": "刪除成功"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# ✅ 補貨：建立 unused 批次（預備庫存）
# ==========================
@ingredients_bp.route("/restock_ingredients", methods=["POST"])
@token_required
def restock_ingredients():
    try:
        if request.user.get("role") != "developer":
            return jsonify({"error": "無權限補貨"}), 403

        store_name = request.user.get("store_name")
        payload = request.get_json() or {}
        items = payload.get("items", [])

        if not isinstance(items, list) or not items:
            return jsonify({"error": "items 必須為陣列且不可為空"}), 400

        for it in items:
            ingredient_id = it.get("ingredient_id")
            add_qty = it.get("add_quantity")
            exp_date = it.get("expiration_date")
            unit = it.get("unit")

            if not ingredient_id or add_qty is None:
                continue

            try:
                add_qty_float = float(add_qty)
            except Exception:
                continue
            if add_qty_float <= 0:
                continue

            ing_ref = db.collection("stores").document(store_name).collection("ingredients").document(ingredient_id)
            snap = ing_ref.get()
            if not snap.exists:
                continue

            ing = snap.to_dict() or {}
            unit_norm = normalize_unit(unit or ing.get("unit"))
            price = float(ing.get("price", 0) or 0)

            # 建立新的 unused 批次（預備庫存）
            ing_ref.collection("batches").add({
                "quantity": float(add_qty_float),
                "original_quantity": float(add_qty_float),
                "unit": unit_norm,
                "expiration_date": exp_date,
                "status": "unused",
                "created_at": firestore.SERVER_TIMESTAMP,
                "restock_record": True,
                "note": "restock -> unused",
                "price": price,
            })

            # 父文件只做「狀態保險」：有備用不代表有使用中，但至少不要卡在奇怪狀態
            # 若原本完全沒 current_batch_id 且 current_quantity == 0，仍維持 out_of_stock（等自動切批次時再轉 in_use）
            # 這邊不強行改 current_quantity
            if ing.get("status") not in ("in_stock", "out_of_stock"):
                ing_ref.update({"status": "in_stock" if float(ing.get("current_quantity", 0) or 0) > 0 else "out_of_stock"})

        return jsonify({"message": "補貨完成"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# 舊功能：依今日已完成訂單扣庫存（⚠ 你現在 orders.py 已即時扣批次庫存，這段不建議再用）
# ==========================
def _mark_orders_refreshed_for_store_today(store_name: str) -> Tuple[int, int]:
    today = taipei_today_str()
    orders_col = (
        db.collection("stores")
        .document(store_name)
        .collection("dates")
        .document(today)
        .collection("completed_orders")
    )

    docs = list(orders_col.where("used_in_inventory_refresh", "==", False).stream())

    batch = db.batch()
    for d in docs:
        batch.update(d.reference, {"used_in_inventory_refresh": True})
    batch.commit()

    total = len(list(orders_col.stream()))
    return len(docs), total


def _refresh_for_store_today(store_name: str) -> Tuple[Dict[str, float], int, str]:
    """
    舊邏輯保留：父文件扣庫存（不建議再用，避免跟批次扣庫存重複）
    """
    today = taipei_today_str()

    orders_col = (
        db.collection("stores")
        .document(store_name)
        .collection("dates")
        .document(today)
        .collection("completed_orders")
    )

    order_docs = list(orders_col.where("used_in_inventory_refresh", "==", False).stream())

    deducted: Dict[str, float] = {}
    processed = 0

    for od in order_docs:
        order = od.to_dict() or {}
        items = order.get("items", [])
        if not isinstance(items, list) or not items:
            continue

        for item in items:
            menu_name = item.get("menu_name")
            qty = item.get("quantity", 1)
            try:
                qty = float(qty)
            except Exception:
                qty = 1.0

            recipe_doc = (
                db.collection("stores")
                .document(store_name)
                .collection("recipes")
                .document(menu_name)
                .get()
            )
            if not recipe_doc.exists:
                continue

            recipe = recipe_doc.to_dict() or {}

            for ing_name, detail in recipe.items():
                amount = float(detail.get("amount", 0) or 0)
                recipe_unit = normalize_unit(detail.get("unit"))

                ing_query = (
                    db.collection("stores")
                    .document(store_name)
                    .collection("ingredients")
                    .where("name", "==", ing_name)
                    .limit(1)
                    .stream()
                )
                ing_doc = next(ing_query, None)
                if not ing_doc:
                    continue

                ing_data = ing_doc.to_dict() or {}
                ingredient_unit = normalize_unit(ing_data.get("unit"))

                if recipe_unit != ingredient_unit:
                    adj = convert_amount(ingredient_unit, recipe_unit, amount)
                else:
                    adj = amount

                consume = float(adj) * float(qty)

                ing_ref = (
                    db.collection("stores")
                    .document(store_name)
                    .collection("ingredients")
                    .document(ing_doc.id)
                )

                snap = ing_ref.get()
                if not snap.exists:
                    continue

                cur = snap.to_dict() or {}
                cur_qty = float(cur.get("quantity", 0) or 0)
                new_qty = cur_qty - consume
                if new_qty < 0:
                    new_qty = 0

                upd = {"quantity": new_qty, "current_quantity": new_qty}
                upd["status"] = "out_of_stock" if new_qty == 0 else "in_stock"
                ing_ref.update(upd)

                deducted[ing_name] = deducted.get(ing_name, 0.0) + consume

        od.reference.update({"used_in_inventory_refresh": True})
        processed += 1

    return deducted, processed, today


@ingredients_bp.route("/refresh_inventory_by_sales", methods=["POST"])
@token_required
def refresh_inventory_by_sales():
    try:
        if request.user.get("role") not in ("developer", "superadmin"):
            return jsonify({"error": "無權限"}), 403

        store_name = request.user.get("store_name")
        deducted, count, ymd = _refresh_for_store_today(store_name)

        return jsonify({
            "message": "扣庫存完成（舊模式）",
            "date": ymd,
            "processed_orders": count,
            "deducted_ingredients": deducted,
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ingredients_bp.route("/cron_refresh_today", methods=["POST"])
def cron_refresh_today():
    auth = request.headers.get("X-CRON-KEY")
    expected = os.getenv("CRON_KEY")
    if expected and auth != expected:
        return jsonify({"error": "unauthorized"}), 401

    results = {}
    today = taipei_today_str()

    for store_doc in db.collection("stores").stream():
        store_name = store_doc.id
        try:
            deducted, count, _ = _refresh_for_store_today(store_name)
            results[store_name] = {
                "processed_orders": count,
                "deducted_ingredients": deducted
            }
        except Exception as e:
            results[store_name] = {"error": str(e)}

    return jsonify({"date": today, "results": results}), 200

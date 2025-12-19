import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import os

# 🔴 改成你自己的 service account 路徑
SERVICE_ACCOUNT_PATH = "yaoyaoproject-88907-firebase-adminsdk-fbsvc-b498692948.json"

cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
firebase_admin.initialize_app(cred)

db = firestore.client()

STORE_ID = "芝山店"   # 🔴 改成你要處理的分店

ingredients_ref = (
    db.collection("stores")
      .document(STORE_ID)
      .collection("ingredients")
)

def migrate_ingredient(doc):
    data = doc.to_dict()
    ingredient_id = doc.id
    ingredient_ref = doc.reference

    print(f"▶ 處理食材：{data.get('name')} ({ingredient_id})")

    # ---------- 1️⃣ 建立 batches 子集合 ----------
    batches_ref = ingredient_ref.collection("batches")
    batches = list(batches_ref.stream())

    current_batch_id = None
    current_quantity = None

    # ---------- 情況 A：已經有 batches ----------
    if batches:
        for i, batch in enumerate(batches):
            batch_data = batch.to_dict()

            # 補 status
            if "status" not in batch_data:
                new_status = "in_use" if i == 0 else "unused"
                batch.reference.update({"status": new_status})
                print(f"  ✔ batch {batch.id} 設為 {new_status}")

            if i == 0:
                current_batch_id = batch.id
                current_quantity = batch_data.get("quantity", 0)

    # ---------- 情況 B：沒有 batches，但父文件有 quantity ----------
    else:
        if "quantity" in data:
            new_batch_ref = batches_ref.document()
            new_batch_ref.set({
                "quantity": data.get("quantity", 0),
                "expiration_date": data.get("expiration_date"),
                "unit": data.get("unit"),
                "price": data.get("price"),
                "status": "in_use",
                "created_at": datetime.now(),
                "note": "系統自動遷移：舊庫存轉批次"
            })
            current_batch_id = new_batch_ref.id
            current_quantity = data.get("quantity", 0)

            print(f"  ✔ 建立新 batch {current_batch_id}")

    # ---------- 2️⃣ 更新父文件 ----------
    update_data = {
        "current_batch_id": current_batch_id,
        "current_quantity": current_quantity,
        "status": "in_stock" if current_quantity and current_quantity > 0 else "out_of_stock"
    }

    # 移除舊欄位
    for field in ["quantity", "expiration_date", "price"]:
        if field in data:
            update_data[field] = firestore.DELETE_FIELD

    ingredient_ref.update(update_data)
    print(f"  ✔ 更新 ingredient 完成\n")


def run_migration():
    docs = ingredients_ref.stream()
    for doc in docs:
        migrate_ingredient(doc)

    print("🎉 所有食材遷移完成")


if __name__ == "__main__":
    run_migration()

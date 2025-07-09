from firebase_config import db

def fix_orders(store_name):
    for collection in ["orders", "completed_orders"]:
        docs = db.collection("stores").document(store_name).collection(collection).stream()
        for doc in docs:
            data = doc.to_dict()
            updated_items = []
            changed = False
            for item in data.get("items", []):
                if not all(k in item for k in ["menu_id", "menu_name", "unit_price", "quantity", "subtotal"]):
                    # 重新查詢菜單資訊
                    menu_id = item.get("menu_id")
                    quantity = item.get("quantity", 1)
                    if not menu_id:
                        continue
                    menu_doc = db.collection("menus").document(menu_id).get()
                    if not menu_doc.exists:
                        continue
                    menu_data = menu_doc.to_dict()
                    unit_price = menu_data["price"]
                    subtotal = unit_price * quantity
                    updated_items.append({
                        "menu_id": menu_id,
                        "menu_name": menu_data["name"],
                        "unit_price": unit_price,
                        "quantity": quantity,
                        "subtotal": subtotal
                    })
                    changed = True
                else:
                    updated_items.append(item)

            if changed:
                print(f"🛠 修正 {collection}/{doc.id}")
                db.collection("stores").document(store_name).collection(collection).document(doc.id).update({
                    "items": updated_items
                })

# 使用方式（請替換 store 名稱）
fix_orders("測試店")

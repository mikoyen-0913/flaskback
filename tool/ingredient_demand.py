# ingredient_demand.py

# === 口味名稱對應表（預測名稱 → 食譜名稱）===
flavor_map = {
    "珍珠奶油": "珍珠鮮奶油",
    "黑芝麻奶油": "黑芝麻鮮奶油",
    # 如有其他對應請在此補上
}

def calculate_total_demand(predicted_sales: dict, recipe_table: dict):
    """
    根據銷售預測數量與食譜表計算總食材需求量

    :param predicted_sales: 各口味預測銷量（如 {'奶油': 120, '巧克力': 95}）
    :param recipe_table: 各口味對應的食譜（ingredient: (amount, unit)）
    :return: dict 格式 {ingredient_name: {"total": 數量, "unit": 單位}}
    """
    demand = {}

    for flavor, count in predicted_sales.items():
        mapped_flavor = flavor_map.get(flavor, flavor)  # ✅ 做名稱對應

        if mapped_flavor not in recipe_table:
            print(f"⚠️ 找不到 {mapped_flavor} 的食譜，跳過")
            continue

        for ingredient, (amount_per_item, unit) in recipe_table[mapped_flavor].items():
            ingredient_name = ingredient.upper()  # 強制轉大寫統一名稱

            if not isinstance(amount_per_item, (int, float)) or not isinstance(count, (int, float)):
                continue  # 防錯

            total_amount = amount_per_item * count

            if ingredient_name not in demand:
                demand[ingredient_name] = {"total": 0, "unit": unit}

            demand[ingredient_name]["total"] += total_amount

    return demand

# weather_from_graphql.py（加強錯誤處理）
import requests

def fetch_weather_from_graphql(lat: float, lon: float, api_key: str):
    url = "https://opendata.cwa.gov.tw/linked/graphql"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    query = {
        "query": f"""
        query town {{
          town(Longitude: {lon}, Latitude: {lat}) {{
            ctyName
            townName
            forecast72hr {{
              Temperature {{
                Time {{ DataTime Temperature }}
              }}
              ProbabilityOfPrecipitation {{
                Time {{ StartTime EndTime ProbabilityOfPrecipitation }}
              }}
            }}
          }}
        }}
        """
    }

    try:
        resp = requests.post(url, json=query, headers=headers, params={"Authorization": api_key})
        resp.raise_for_status()
        data = resp.json()
        print("🔍 GraphQL 回傳資料預覽：", data)
    except Exception as e:
        print("❌ 無法連線 GraphQL API：", e)
        return None

    try:
        if "data" not in data or "town" not in data["data"] or not data["data"]["town"]:
            print("⚠️ 查無對應地點天氣資料（town 為空）")
            return None

        town_data = data['data']['town']
        temp_list = town_data['forecast72hr']['Temperature']['Time']
        rain_list = town_data['forecast72hr']['ProbabilityOfPrecipitation']['Time']

        results = []
        for i in range(min(3, len(temp_list), len(rain_list))):
            temp = int(temp_list[i]['Temperature'])
            rain = int(rain_list[i]['ProbabilityOfPrecipitation'])
            results.append({"temperature": temp, "rainfall": rain})

        print(f"📍 預測地點：{town_data['ctyName']} {town_data['townName']}")
        for i, r in enumerate(results, 1):
            print(f"  第 {i} 天 → 溫度: {r['temperature']}°C, 降雨: {r['rainfall']}%")

        return results

    except Exception as e:
        print("❌ 解析資料失敗：", e)
        return None

if __name__ == "__main__":
    lat = float(input("請輸入緯度："))
    lon = float(input("請輸入經度："))
    api_key = "CWA-6FAB80A3-75EF-4555-86C3-A026F8F0E564"

    result = fetch_weather_from_graphql(lat, lon, api_key)
    if result:
        print("\n✅ 未來三天天氣資料：", result)

"""
Парсит реальные банкоматы Юнусабадского района Ташкента
через OpenStreetMap Overpass API и сохраняет в real_atms.json + real_atms.csv
"""

import json
import time
import requests
import pandas as pd

# Bounding box Юнусабадского района Ташкента
# (south, west, north, east)
BBOX = (41.330, 69.260, 41.375, 69.320)

OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

QUERY = (
    f"[out:json][timeout:30];"
    f"("
    f"node[amenity=atm]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});"
    f"node[amenity=bank]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});"
    f");"
    f"out body;"
)

def fetch_atms():
    print("Запрос к Overpass API (OpenStreetMap)...")
    headers = {"User-Agent": "ATM-Dashboard-Research/1.0"}
    for mirror in OVERPASS_MIRRORS:
        try:
            print(f"  Пробую: {mirror}")
            resp = requests.get(mirror, params={"data": QUERY},
                                headers=headers, timeout=40)
            resp.raise_for_status()
            data = resp.json()
            print(f"  Успешно!")
            break
        except Exception as e:
            print(f"  Ошибка: {e}")
            time.sleep(2)
    else:
        return []

    elements = data.get("elements", [])
    print(f"Найдено элементов: {len(elements)}")

    atms = []
    for el in elements:
        tags = el.get("tags", {})
        lat  = el.get("lat")
        lon  = el.get("lon")
        if lat is None or lon is None:
            continue

        name    = (tags.get("name:ru")
                or tags.get("name:en")
                or tags.get("name")
                or tags.get("operator")
                or "ATM")
        bank    = (tags.get("operator")
                or tags.get("brand")
                or tags.get("name")
                or "Unknown")
        address = " ".join(filter(None, [
            tags.get("addr:street", ""),
            tags.get("addr:housenumber", ""),
        ])) or tags.get("addr:full", "")
        amenity = tags.get("amenity", "atm")

        atms.append({
            "osm_id":   el.get("id"),
            "amenity":  amenity,
            "name":     name,
            "bank":     bank,
            "address":  address,
            "lat":      lat,
            "lon":      lon,
            "opening_hours": tags.get("opening_hours", ""),
            "currency": tags.get("currency:UZS", ""),
            "network":  tags.get("network", ""),
            "raw_tags": json.dumps(tags, ensure_ascii=False),
        })

    return atms


def main():
    atms = fetch_atms()

    if not atms:
        print("Ничего не найдено. Попробуй позже.")
        return

    # Сохраняем JSON
    with open("real_atms.json", "w", encoding="utf-8") as f:
        json.dump(atms, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: real_atms.json ({len(atms)} записей)")

    # Сохраняем CSV
    df = pd.DataFrame(atms).drop(columns=["raw_tags"])
    df.to_csv("real_atms.csv", index=False, encoding="utf-8-sig")
    print(f"Сохранено: real_atms.csv")

    # Печатаем таблицу
    print(f"\n{'№':<4} {'name':<35} {'bank':<25} {'lat':<10} {'lon':<10} {'address'}")
    print("-" * 110)
    for i, a in enumerate(atms, 1):
        print(f"{i:<4} {a['name'][:34]:<35} {a['bank'][:24]:<25} {a['lat']:<10} {a['lon']:<10} {a['address']}")

    print(f"\nИтого: {len(atms)} объектов")

    # Формируем готовый ATM_LIST для generate_atm_dataset.py
    only_atms = [a for a in atms if a["amenity"] == "atm"]
    print(f"\n# ATM_LIST для generate_atm_dataset.py ({len(only_atms)} банкоматов):")
    print("ATM_LIST = [")
    for i, a in enumerate(only_atms):
        atm_id = f"atm{i+1:03d}"
        cap = 50_000_000
        print(f'    ("{atm_id}", "{a["name"][:40]}", "{a["bank"][:30]}", "{a["address"] or a["name"]}", {a["lat"]}, {a["lon"]}, {cap}),')
    print("]")


if __name__ == "__main__":
    main()

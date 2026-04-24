import json

with open("C:/Users/gruto/OneDrive/Desktop/ATM/real_atms.json", encoding="utf-8") as f:
    data = json.load(f)

atms = [a for a in data if a["amenity"] == "atm"]
print(f"Только ATM (не банки): {len(atms)}\n")
for i, a in enumerate(atms, 1):
    print(f"{i:2}. {a['name'][:45]:45} | {a['bank'][:25]:25} | {a['lat']} {a['lon']}")

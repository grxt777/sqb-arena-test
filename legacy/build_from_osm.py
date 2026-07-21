"""
Читает real_atms.json (данные OSM) и:
1. Обновляет ATM_LIST в generate_atm_dataset.py
2. Обновляет ATM_META в dashboard/index.html
3. Перезапускает генерацию CSV
"""

import json
import re
import subprocess
import sys

OSM_FILE       = "C:/Users/gruto/OneDrive/Desktop/ATM/real_atms.json"
GENERATOR_FILE = "C:/Users/gruto/OneDrive/Desktop/ATM/generate_atm_dataset.py"
DASHBOARD_FILE = "C:/Users/gruto/OneDrive/Desktop/ATM/dashboard/index.html"

# Нормализуем имена банков (кириллица → латиница)
BANK_ALIASES = {
    "unknown":         "Unknown",
    "ipotekabank":     "Ipoteka Bank",
    "ipateka kassa 15":"Ipoteka Bank",
    "xalq banki":      "Xalq Bank",
    "xalq bank kassa": "Xalq Bank",
    "ipak yo'li banki":"Ipak Yo'li Bank",
    "ipak yo`li banki":"Ipak Yo'li Bank",
    "ipak yoli banki": "Ipak Yo'li Bank",
    "ipak yo\u2019li bank": "Ipak Yo'li Bank",
    "kapitalbank":     "Kapitalbank",
    "kapital bank":    "Kapitalbank",
    "agrobank":        "Agrobank",
    "hamkorbank":      "Hamkorbank",
    "hamkor bank":     "Hamkorbank",
    "asia alliance bank": "Asia Alliance Bank",
    "orient finans bank (ofb)": "Orient Finans Bank",
    "orient finans bank": "Orient Finans Bank",
    "ofb":             "Orient Finans Bank",
    "savdogarbank":    "Savdogarbank",
    "tenge bank":      "Tenge Bank",
    "avo":             "AVO",
    "turon telecom":   "Turon Bank",
    "turon bank mini": "Turon Bank",
}

CAPACITY_BY_BANK = {
    "Xalq Bank":        70_000_000,
    "Ipoteka Bank":     55_000_000,
    "Ipak Yo'li Bank":  60_000_000,
    "Kapitalbank":      50_000_000,
    "Agrobank":         45_000_000,
    "Hamkorbank":       40_000_000,
    "Asia Alliance Bank": 45_000_000,
    "Orient Finans Bank": 40_000_000,
    "Savdogarbank":     35_000_000,
    "Tenge Bank":       40_000_000,
    "Turon Bank":       35_000_000,
    "AVO":              30_000_000,
}
DEFAULT_CAPACITY = 50_000_000


def normalize_bank(raw: str) -> str:
    key = raw.strip().lower()
    return BANK_ALIASES.get(key, raw.strip() or "Unknown")


def normalize_name(name: str, bank: str, idx: int) -> str:
    n = name.strip()
    # Если имя кириллическое или "ATM" — используем банк + номер
    has_latin = any(c.isascii() and c.isalpha() for c in n)
    if not has_latin or n.upper() in ("ATM", ""):
        return f"{bank} #{idx}"
    return n


def load_atms():
    with open(OSM_FILE, encoding="utf-8") as f:
        data = json.load(f)
    # только amenity=atm
    atms = [a for a in data if a["amenity"] == "atm"]

    result = []
    for i, a in enumerate(atms, 1):
        bank = normalize_bank(a["bank"])
        name = normalize_name(a["name"], bank, i)
        cap  = CAPACITY_BY_BANK.get(bank, DEFAULT_CAPACITY)
        addr = a["address"] or name
        result.append({
            "id":      f"atm{i:03d}",
            "name":    name,
            "bank":    bank,
            "address": addr,
            "lat":     a["lat"],
            "lon":     a["lon"],
            "capacity": cap,
        })
    return result


def build_atm_list_py(atms):
    lines = ["ATM_LIST = ["]
    for a in atms:
        lines.append(
            f'    ("{a["id"]}", "{a["name"]}", "{a["bank"]}", '
            f'"{a["address"]}", {a["lat"]}, {a["lon"]}, {a["capacity"]}),')
    lines.append("]")
    return "\n".join(lines)


def build_atm_meta_js(atms):
    lines = ["const ATM_META = ["]
    for a in atms:
        name    = a["name"].replace('"', '\\"')
        bank    = a["bank"].replace('"', '\\"')
        address = a["address"].replace('"', '\\"')
        lines.append(
            f'  {{ id:"{a["id"]}", name:"{name}", bank:"{bank}", '
            f'address:"{address}", lat:{a["lat"]}, lon:{a["lon"]}, '
            f'capacity:{a["capacity"]} }},')
    lines.append("];")
    return "\n".join(lines)


def patch_generator(atms):
    with open(GENERATOR_FILE, encoding="utf-8") as f:
        src = f.read()

    new_list = build_atm_list_py(atms)
    # заменяем блок ATM_LIST = [ ... ]
    src = re.sub(
        r"ATM_LIST\s*=\s*\[.*?\]",
        new_list,
        src,
        flags=re.DOTALL,
    )
    with open(GENERATOR_FILE, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"  generate_atm_dataset.py обновлён ({len(atms)} ATM)")


def patch_dashboard(atms):
    with open(DASHBOARD_FILE, encoding="utf-8") as f:
        src = f.read()

    new_meta = build_atm_meta_js(atms)
    src = re.sub(
        r"const ATM_META\s*=\s*\[.*?\];",
        new_meta,
        src,
        flags=re.DOTALL,
    )
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"  dashboard/index.html обновлён ({len(atms)} ATM)")


def main():
    atms = load_atms()
    print(f"Загружено из OSM: {len(atms)} банкоматов\n")

    for a in atms:
        print(f"  {a['id']}  {a['name'][:40]:40} | {a['bank']:25} | {a['lat']} {a['lon']}")

    print("\nОбновляю файлы...")
    patch_generator(atms)
    patch_dashboard(atms)

    print("\nПерегенерирую CSV...")
    result = subprocess.run(
        [sys.executable, GENERATOR_FILE],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        # последние 3 строки вывода
        for line in result.stdout.strip().splitlines()[-3:]:
            print(" ", line)
        print("\nГотово!")
    else:
        print("Ошибка генерации CSV:")
        print(result.stderr)


if __name__ == "__main__":
    main()

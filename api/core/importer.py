"""
Парсер XLSX с реестром банкоматов.

Ожидаемая структура (нумерация по EXCEL-столбцам, 1-based):
  A — Номер банкомата
  B — Номер по филиалам
  C — Локал код
  D — Область
  E — Филиал
  F — Модель банкомата
  G — Тармоқ тизими тури (тип сети)
  H — Серия рақами
  I — Мерчант ID
  J — Терминал ID
  K — Адрес
  L — Геолокация (lat)
  M — Геолокация (lon)

Первая строка — заголовок.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
from openpyxl.utils import get_column_letter

from .config import DEFAULT_CAPACITY

log = logging.getLogger(__name__)


# Синонимы заголовков (в lower-case, без пробелов) → каноническое имя поля
HEADER_ALIASES: Dict[str, str] = {
    # ATM
    "номербанкомата": "atm_number",
    "номер_банкомата": "atm_number",
    "atm_number": "atm_number",
    "atmnumber": "atm_number",
    "atm": "atm_number",

    # Branch code
    "номерпофилиалам": "branch_code",
    "номер_по_филиалам": "branch_code",
    "branch_code": "branch_code",
    "branchcode": "branch_code",

    # Local code
    "локалкод": "local_code",
    "локал_код": "local_code",
    "local_code": "local_code",
    "localcode": "local_code",
    "локал": "local_code",

    # Region
    "область": "region",
    "region": "region",
    "viloyat": "region",

    # Branch
    "филиал": "branch",
    "branch": "branch",

    # Model
    "модельбанкомата": "model",
    "модель_банкомата": "model",
    "model": "model",
    "модель": "model",

    # Network type
    "тармоқтизимитури": "network_type",
    "тармоктизимитури": "network_type",
    "network_type": "network_type",
    "networktype": "network_type",
    "тармок": "network_type",

    # Serial
    "серияраками": "serial",
    "серия_раками": "serial",
    "серия_рақами": "serial",   # совместимость (нормализатор всё равно приведёт)
    "serial": "serial",
    "серия": "serial",

    # Merchant ID
    "мерчантайди": "merchant_id",
    "мерчант_айди": "merchant_id",
    "мерчантid": "merchant_id",
    "merchant_id": "merchant_id",
    "merchantid": "merchant_id",

    # Terminal ID
    "терминалайди": "terminal_id",
    "терминал_айди": "terminal_id",
    "терминалid": "terminal_id",
    "terminal_id": "terminal_id",
    "terminalid": "terminal_id",
    "term_id": "terminal_id",
    "termid": "terminal_id",

    # Address
    "адрес": "address",
    "address": "address",
    "manzil": "address",

    # Lat / Lon
    "геолокация(alt)": "lat",
    "геолокация_alt": "lat",
    "геолокация(лат)": "lat",
    "геолокация(широта)": "lat",
    "геолокацияlat": "lat",
    "геолокация": "lat",
    "lat": "lat",
    "latitude": "lat",
    "широта": "lat",

    "геолокация(long)": "lon",
    "геолокация_long": "lon",
    "геолокация(долгота)": "lon",
    "геолокацияlon": "lon",
    "lon": "lon",
    "lng": "lon",
    "longitude": "lon",
    "долгота": "lon",
}


REQUIRED_FIELDS = ("terminal_id",)


def _normalize_header(text: Any) -> str:
    """Нормализует заголовок: нижний регистр, без пробелов/знаков препинания."""
    if text is None:
        return ""
    s = str(text).strip().lower()
    # Схлопываем кириллические спец-символы к их ASCII-аналогам
    cyr_map = {
        "қ": "к", "қ": "к", "ҳ": "х", "ғ": "г",
        "ў": "у", "қ": "к", "Қ": "к", "Ҳ": "х",
        "Ў": "у", "Ғ": "г",
    }
    for src, dst in cyr_map.items():
        s = s.replace(src, dst)
    s = re.sub(r"[\s_\-\.,()]+", "", s)
    s = s.replace("'", "").replace("`", "").replace("'", "")
    return s


def _parse_coord(value: Any) -> Optional[float]:
    """Парсит координату из строки или числа."""
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d.\-]", "", value.replace(",", "."))
            try:
                v = float(cleaned)
            except (TypeError, ValueError):
                return None
        else:
            return None
    if -180 <= v <= 180:
        return round(v, 6)
    return None


def _parse_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d\-]", "", value)
            if cleaned.lstrip("-").isdigit():
                try:
                    return int(cleaned)
                except ValueError:
                    return None
        return None


def _parse_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _match_headers(ws) -> Tuple[Dict[int, str], int]:
    """
    Возвращает:
      - словарь {column_index_1based: canonical_field_name}
      - номер строки заголовка (1-based)

    Если в первой строке не нашлось ни одного знакомого заголовка —
    считаем, что первая строка — данные, и поля выводятся по позиции.
    """
    header_row_idx = None
    for row_idx in range(1, min(6, ws.max_row + 1)):
        row = next(ws.iter_rows(min_row=row_idx, max_row=row_idx, values_only=True))
        normalized = [_normalize_header(c) for c in row]
        hits = sum(1 for n in normalized if n in HEADER_ALIASES)
        if hits >= 3:
            header_row_idx = row_idx
            break

    column_map: Dict[int, str] = {}

    if header_row_idx is None:
        # fallback: маппим по позициям A..M
        position_map = {
            1: "atm_number",
            2: "branch_code",
            3: "local_code",
            4: "region",
            5: "branch",
            6: "model",
            7: "network_type",
            8: "serial",
            9: "merchant_id",
            10: "terminal_id",
            11: "address",
            12: "lat",
            13: "lon",
        }
        for col_idx, field in position_map.items():
            column_map[col_idx] = field
        log.warning(
            "Заголовки XLSX не распознаны — используется позиционный маппинг A..M"
        )
        return column_map, 1

    header_row = next(
        ws.iter_rows(min_row=header_row_idx, max_row=header_row_idx, values_only=True)
    )
    for col_idx, cell in enumerate(header_row, start=1):
        norm = _normalize_header(cell)
        if norm in HEADER_ALIASES:
            column_map[col_idx] = HEADER_ALIASES[norm]
    return column_map, header_row_idx


def parse_xlsx(
    path: str | Path,
    sheet_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Парсит XLSX и возвращает:
      {
        "records":     List[Dict]   — распарсенные записи
        "errors":      List[Dict]   — список ошибок построчно
        "total_rows":  int
        "header_row":  int
        "columns":     Dict[int, str]   — маппинг колонок
      }
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active

    column_map, header_row = _match_headers(ws)

    if "terminal_id" not in column_map.values():
        wb.close()
        raise ValueError(
            "Не удалось определить колонку Terminal ID. "
            "Проверьте заголовки XLSX."
        )

    records: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    seen_terminal_ids: set[str] = set()
    total_rows = 0

    for row_idx, row in enumerate(
        ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1
    ):
        total_rows += 1
        if not row or all(c is None or (isinstance(c, str) and not c.strip()) for c in row):
            continue

        record: Dict[str, Any] = {}
        for col_idx, field in column_map.items():
            if col_idx - 1 >= len(row):
                value = None
            else:
                value = row[col_idx - 1]
            if field in ("lat", "lon"):
                record[field] = _parse_coord(value)
            elif field == "capacity":
                record[field] = _parse_int(value) or DEFAULT_CAPACITY
            else:
                record[field] = _parse_str(value)

        terminal_id = record.get("terminal_id")
        if not terminal_id:
            errors.append({
                "row": row_idx,
                "error": "Пустой Terminal ID",
            })
            continue

        if terminal_id in seen_terminal_ids:
            errors.append({
                "row": row_idx,
                "terminal_id": terminal_id,
                "error": "Дубликат Terminal ID в файле",
            })
            continue
        seen_terminal_ids.add(terminal_id)

        record.setdefault("capacity", DEFAULT_CAPACITY)
        records.append(record)

    wb.close()

    return {
        "records": records,
        "errors": errors,
        "total_rows": total_rows,
        "header_row": header_row,
        "columns": {get_column_letter(k): v for k, v in column_map.items()},
    }

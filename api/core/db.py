"""
SQLite-хранилище для ATM, загруженных из XLSX.

Таблица atms хранит:
  - terminal_id   — уникальный ID терминала (из колонки J)
  - atm_number    — номер банкомата (A)
  - branch_code   — номер по филиалам (B)
  - local_code    — локал код (C)
  - region        — область (D)
  - branch        — филиал (E)
  - model         — модель ATM (F)
  - network_type  — тармок тизими тури (G) — тип сети
  - serial        — серия раками (H)
  - merchant_id   — мерчант ID (I)
  - address       — адрес (K)
  - lat           — геолокация (L)
  - lon           — геолокация (M)
  - balance       — текущий остаток (заполняется отдельно, по умолчанию NULL)
  - capacity      — ёмкость (по умолчанию 400 000 000 UZS)
  - status        — ok | warning | critical (по балансу)
  - created_at / updated_at
"""

from __future__ import annotations

import sqlite3
import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Dict, Optional, Any

from .config import DATA_DIR, DEFAULT_CAPACITY, LOW_CASH_PCT, WARNING_CASH_PCT

log = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "atms.db"
_lock = threading.Lock()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Создаёт таблицы при первом запуске."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS atms (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                terminal_id     TEXT UNIQUE NOT NULL,
                atm_number      TEXT,
                branch_code     TEXT,
                local_code      TEXT,
                region          TEXT,
                branch          TEXT,
                model           TEXT,
                network_type    TEXT,
                serial          TEXT,
                merchant_id     TEXT,
                address         TEXT,
                lat             REAL,
                lon             REAL,
                capacity        INTEGER NOT NULL DEFAULT 400000000,
                balance         INTEGER,
                last_balance_at TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_atms_region ON atms(region);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_atms_branch ON atms(branch);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_atms_local ON atms(local_code);")
    log.info("БД инициализирована: %s", DB_PATH)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _compute_status(balance: Optional[int], capacity: int) -> str:
    if balance is None:
        return "unknown"
    if capacity <= 0:
        return "ok"
    pct = balance / capacity
    if pct < LOW_CASH_PCT:
        return "critical"
    if pct < WARNING_CASH_PCT:
        return "warning"
    return "ok"


def count_atms() -> int:
    with _connect() as conn:
        cur = conn.execute("SELECT COUNT(*) AS c FROM atms;")
        return int(cur.fetchone()["c"])


def list_atms(
    region: Optional[str] = None,
    branch: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM atms WHERE 1=1"
    params: list[Any] = []
    if region:
        sql += " AND region = ?"
        params.append(region)
    if branch:
        sql += " AND branch = ?"
        params.append(branch)
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = _row_to_dict(row)
        cap = d.get("capacity") or DEFAULT_CAPACITY
        bal = d.get("balance")
        d["balance_pct"] = round((bal / cap) * 100, 1) if bal is not None and cap else None
        d["status"] = _compute_status(bal, cap)
        result.append(d)
    if status:
        result = [r for r in result if r["status"] == status]
    return result


def get_atm(terminal_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM atms WHERE terminal_id = ?", (terminal_id,)
        ).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    cap = d.get("capacity") or DEFAULT_CAPACITY
    bal = d.get("balance")
    d["balance_pct"] = round((bal / cap) * 100, 1) if bal is not None and cap else None
    d["status"] = _compute_status(bal, cap)
    return d


def list_regions() -> List[Dict[str, Any]]:
    """Возвращает список областей с количеством ATM."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT region, COUNT(*) AS cnt
            FROM atms
            WHERE region IS NOT NULL AND region != ''
            GROUP BY region
            ORDER BY cnt DESC;
            """
        ).fetchall()
    return [{"region": r["region"], "count": r["cnt"]} for r in rows]


def list_branches(region: Optional[str] = None) -> List[Dict[str, Any]]:
    """Возвращает список филиалов с количеством ATM."""
    sql = """
        SELECT branch, region, COUNT(*) AS cnt
        FROM atms
        WHERE branch IS NOT NULL AND branch != ''
    """
    params: list[Any] = []
    if region:
        sql += " AND region = ?"
        params.append(region)
    sql += " GROUP BY branch, region ORDER BY cnt DESC;"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"branch": r["branch"], "region": r["region"], "count": r["cnt"]}
        for r in rows
    ]


# ── Массовый импорт ───────────────────────────────────────────

def truncate_atms() -> int:
    """Удаляет все ATM. Возвращает количество удалённых строк."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM atms;")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='atms';")
        return cur.rowcount


def bulk_insert_atms(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Массовая вставка/обновление ATM по terminal_id.
    Использует UPSERT (ON CONFLICT).
    Возвращает статистику.
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    with _connect() as conn:
        for rec in records:
            terminal_id = (rec.get("terminal_id") or "").strip()
            if not terminal_id:
                stats["skipped"] += 1
                continue

            existing = conn.execute(
                "SELECT id FROM atms WHERE terminal_id = ?", (terminal_id,)
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE atms SET
                        atm_number   = ?,
                        branch_code  = ?,
                        local_code   = ?,
                        region       = ?,
                        branch       = ?,
                        model        = ?,
                        network_type = ?,
                        serial       = ?,
                        merchant_id  = ?,
                        address      = ?,
                        lat          = ?,
                        lon          = ?,
                        capacity     = ?,
                        updated_at   = datetime('now')
                    WHERE terminal_id = ?
                    """,
                    (
                        rec.get("atm_number"),
                        rec.get("branch_code"),
                        rec.get("local_code"),
                        rec.get("region"),
                        rec.get("branch"),
                        rec.get("model"),
                        rec.get("network_type"),
                        rec.get("serial"),
                        rec.get("merchant_id"),
                        rec.get("address"),
                        rec.get("lat"),
                        rec.get("lon"),
                        rec.get("capacity") or DEFAULT_CAPACITY,
                        terminal_id,
                    ),
                )
                stats["updated"] += 1
            else:
                conn.execute(
                    """
                    INSERT INTO atms (
                        terminal_id, atm_number, branch_code, local_code,
                        region, branch, model, network_type, serial,
                        merchant_id, address, lat, lon, capacity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        terminal_id,
                        rec.get("atm_number"),
                        rec.get("branch_code"),
                        rec.get("local_code"),
                        rec.get("region"),
                        rec.get("branch"),
                        rec.get("model"),
                        rec.get("network_type"),
                        rec.get("serial"),
                        rec.get("merchant_id"),
                        rec.get("address"),
                        rec.get("lat"),
                        rec.get("lon"),
                        rec.get("capacity") or DEFAULT_CAPACITY,
                    ),
                )
                stats["inserted"] += 1
    return stats


def update_balance(terminal_id: str, balance: int) -> bool:
    """Обновляет баланс одного ATM. Возвращает True если обновлено."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE atms
               SET balance = ?,
                   last_balance_at = datetime('now'),
                   updated_at = datetime('now')
             WHERE terminal_id = ?
            """,
            (int(balance), terminal_id),
        )
        return cur.rowcount > 0


def bulk_update_balances(updates: List[Dict[str, Any]]) -> Dict[str, int]:
    """Массовое обновление балансов. updates: [{terminal_id, balance}, ...]"""
    ok, fail = 0, 0
    with _connect() as conn:
        for u in updates:
            tid = u.get("terminal_id")
            bal = u.get("balance")
            if not tid or bal is None:
                fail += 1
                continue
            cur = conn.execute(
                """
                UPDATE atms
                   SET balance = ?,
                       last_balance_at = datetime('now'),
                       updated_at = datetime('now')
                 WHERE terminal_id = ?
                """,
                (int(bal), tid),
            )
            if cur.rowcount:
                ok += 1
            else:
                fail += 1
    return {"updated": ok, "failed": fail}

"""Registro local de mensajes salientes por mes."""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple


BASE_DIR = os.path.dirname(__file__)
STATS_FILE = os.path.join(BASE_DIR, "message_stats.sqlite3")
LOCAL_TZ = timezone(timedelta(hours=-3))
MONTH_NAMES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _local_datetime(value: Optional[datetime] = None) -> datetime:
    """Convierte una fecha a UTC-3; una fecha sin zona se interpreta como UTC."""
    if value is None:
        value = datetime.now(timezone.utc)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TZ)


def month_key(value: Optional[datetime] = None) -> str:
    """Devuelve la clave YYYY-MM para la fecha local UTC-3."""
    return _local_datetime(value).strftime("%Y-%m")


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path or STATS_FILE, timeout=5)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS monthly_message_counts (
            month TEXT PRIMARY KEY,
            sent_count INTEGER NOT NULL DEFAULT 0 CHECK (sent_count >= 0)
        )
        """
    )
    return connection


def record_message_sent(value: Optional[datetime] = None, db_path: Optional[str] = None) -> bool:
    """Registra un mensaje ya enviado correctamente y devuelve si pudo guardarlo."""
    connection = None
    try:
        connection = _connect(db_path)
        connection.execute(
            """
            INSERT INTO monthly_message_counts (month, sent_count)
            VALUES (?, 1)
            ON CONFLICT(month) DO UPDATE SET sent_count = sent_count + 1
            """,
            (month_key(value),),
        )
        connection.commit()
        return True
    except sqlite3.Error as error:
        print(f"[WARN] No se pudo registrar el contador de mensajes: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()


def _recent_month_keys(value: Optional[datetime], months: int) -> List[str]:
    if months < 1:
        raise ValueError("months debe ser al menos 1")

    current = _local_datetime(value)
    keys = []
    for _ in range(months):
        keys.append(f"{current.year:04d}-{current.month:02d}")
        current = current.replace(day=1) - timedelta(days=1)
    return keys


def get_recent_counts(
    months: int = 3,
    value: Optional[datetime] = None,
    db_path: Optional[str] = None,
) -> List[Tuple[str, int]]:
    """Obtiene los meses recientes, incluyendo los que todavía no tienen envíos."""
    keys = _recent_month_keys(value, months)
    connection = None
    try:
        connection = _connect(db_path)
        rows = connection.execute(
            "SELECT month, sent_count FROM monthly_message_counts WHERE month IN ({})".format(
                ",".join("?" for _ in keys)
            ),
            keys,
        ).fetchall()
    except sqlite3.Error as error:
        print(f"[WARN] No se pudo consultar el contador de mensajes: {error}")
        rows = []
    finally:
        if connection is not None:
            connection.close()

    counts = dict(rows)
    return [(key, int(counts.get(key, 0))) for key in keys]


def get_latest_recorded_counts(
    limit: int = 3,
    db_path: Optional[str] = None,
) -> List[Tuple[str, int]]:
    """Lee hasta ``limit`` meses con mensajes, sin crear ni modificar la base SQLite."""
    if limit < 1:
        raise ValueError("limit debe ser al menos 1")

    path = os.path.abspath(db_path or STATS_FILE)
    if not os.path.exists(path):
        return []

    connection = None
    try:
        database_uri = "file:{}?mode=ro".format(path.replace("\\", "/"))
        connection = sqlite3.connect(database_uri, uri=True, timeout=5)
        rows = connection.execute(
            """
            SELECT month, sent_count
            FROM monthly_message_counts
            WHERE sent_count > 0
            ORDER BY month DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.Error as error:
        print(f"[WARN] No se pudo leer el historial de mensajes: {error}")
        return []
    finally:
        if connection is not None:
            connection.close()

    return [(str(month), int(count)) for month, count in reversed(rows)]


def month_label(key: str) -> str:
    """Convierte una clave YYYY-MM en un nombre de mes legible en español."""
    year, month = key.split("-", 1)
    return f"{MONTH_NAMES[int(month) - 1]} de {year}"


def build_counter_message(
    value: Optional[datetime] = None,
    db_path: Optional[str] = None,
) -> str:
    """Construye el reporte de tres meses, incluyendo la respuesta CONTADOR actual."""
    counts = get_recent_counts(months=3, value=value, db_path=db_path)
    current_key, current_count = counts[0]
    counts[0] = (current_key, current_count + 1)

    lines = ["Mensajes enviados"]
    lines.extend(f"- {month_label(key)}: {count}" for key, count in counts)
    return "\n".join(lines)

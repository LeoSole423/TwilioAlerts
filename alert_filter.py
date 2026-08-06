"""Filtro persistente de alertas automáticas repetidas por cámara."""

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from message_stats import BASE_DIR


FILTER_DB = os.path.join(BASE_DIR, "alert_filter.sqlite3")
LOCK_DIRECTORY = BASE_DIR
DEFAULT_WINDOW_SECONDS = 10.0


@dataclass(frozen=True)
class FilterDecision:
    should_send: bool
    would_filter: bool
    reason: str
    elapsed_seconds: Optional[float] = None


class CameraLock:
    """Lock no bloqueante por cámara, compatible con Windows y Unix."""

    def __init__(self, camera: str, lock_directory: str = LOCK_DIRECTORY):
        digest = hashlib.sha256(camera.encode("utf-8")).hexdigest()[:16]
        self.path = os.path.join(lock_directory, f"alert_filter_{digest}.lock")
        self.handle = None
        self.acquired = False

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        try:
            if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
                with open(self.path, "ab") as initializer:
                    if initializer.tell() == 0:
                        initializer.write(b"0")
                        initializer.flush()
            self.handle = open(self.path, "r+b")
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.release()
        return self.acquired

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if self.acquired:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
            self.acquired = False


def camera_from_filename(filename: str) -> Optional[str]:
    """Extrae el nombre de cámara del prefijo anterior al primer punto."""
    camera = os.path.basename(filename).split(".", 1)[0].strip()
    return camera or None


def _positive_window(value: Any) -> float:
    try:
        window = float(value)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_SECONDS
    return window if window > 0 else DEFAULT_WINDOW_SECONDS


def _connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=5)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS camera_alert_state (
            camera TEXT PRIMARY KEY,
            last_image TEXT NOT NULL,
            last_event_ts REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def evaluate_alert(
    camera: Optional[str],
    image_name: str,
    event_timestamp: float,
    settings: Dict[str, Any],
    db_path: str = FILTER_DB,
) -> FilterDecision:
    """Decide si una alerta automática debe enviarse, sin modificar el estado."""
    if not settings.get("alert_filter_enabled", False):
        return FilterDecision(True, False, "disabled")
    if not camera:
        return FilterDecision(True, False, "camera_unknown")

    connection = None
    try:
        connection = _connect(db_path)
        row = connection.execute(
            "SELECT last_image, last_event_ts FROM camera_alert_state WHERE camera = ?",
            (camera,),
        ).fetchone()
    except sqlite3.Error as error:
        print(f"[WARN] No se pudo consultar el filtro de alertas: {error}")
        return FilterDecision(True, False, "state_unavailable")
    finally:
        if connection is not None:
            connection.close()

    if row is None:
        return FilterDecision(True, False, "first_alert")

    last_image, last_event_ts = row
    if image_name == last_image:
        return _apply_log_only(settings, "same_image")
    if event_timestamp <= float(last_event_ts):
        return _apply_log_only(settings, "stale_event")

    elapsed = event_timestamp - float(last_event_ts)
    if elapsed < _positive_window(settings.get("alert_filter_window_seconds")):
        return _apply_log_only(settings, "cooldown", elapsed)
    return FilterDecision(True, False, "window_elapsed", elapsed)


def _apply_log_only(settings: Dict[str, Any], reason: str, elapsed: Optional[float] = None) -> FilterDecision:
    if settings.get("alert_filter_log_only", False):
        return FilterDecision(True, True, reason, elapsed)
    return FilterDecision(False, True, reason, elapsed)


def remember_sent_alert(
    camera: Optional[str],
    image_name: str,
    event_timestamp: float,
    settings: Dict[str, Any],
    db_path: str = FILTER_DB,
) -> bool:
    """Persiste solo una alerta que Twilio haya aceptado para al menos un destinatario."""
    if not settings.get("alert_filter_enabled", False) or not camera:
        return False

    connection = None
    try:
        connection = _connect(db_path)
        connection.execute(
            """
            INSERT INTO camera_alert_state (camera, last_image, last_event_ts, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(camera) DO UPDATE SET
                last_image = excluded.last_image,
                last_event_ts = excluded.last_event_ts,
                updated_at = excluded.updated_at
            """,
            (camera, image_name, event_timestamp, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
        return True
    except sqlite3.Error as error:
        print(f"[WARN] No se pudo guardar el filtro de alertas: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()

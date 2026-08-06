"""Sincroniza el contador local de Pilares con el webhook de n8n."""

import json
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

import requests

from message_stats import BASE_DIR, STATS_FILE, get_latest_recorded_counts


SETTINGS_FILE = os.path.join(BASE_DIR, "Settings.json")
LOCK_FILE = os.path.join(BASE_DIR, "pilares_sync.lock")
EXPECTED_INSTANCE_ID = "Pilares"
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 8


def _load_settings(settings_path: str = SETTINGS_FILE) -> Dict[str, Any]:
    with open(settings_path, "r", encoding="utf-8") as settings_file:
        return json.load(settings_file)


def _positive_number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def get_sync_interval_seconds(settings: Optional[Dict[str, Any]] = None) -> float:
    """Obtiene un intervalo positivo, con 300 segundos como valor seguro."""
    settings = settings if settings is not None else _load_settings()
    return _positive_number(settings.get("pilares_sync_interval_seconds"), DEFAULT_INTERVAL_SECONDS)


@contextmanager
def _try_sync_lock(lock_path: str = LOCK_FILE) -> Iterator[bool]:
    """Adquiere un lock no bloqueante compatible con Windows y Unix."""
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    try:
        if not os.path.exists(lock_path) or os.path.getsize(lock_path) == 0:
            with open(lock_path, "ab") as initializer:
                if initializer.tell() == 0:
                    initializer.write(b"0")
                    initializer.flush()
        lock_handle = open(lock_path, "r+b")
    except OSError:
        yield False
        return

    acquired = False
    try:
        lock_handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl

            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False
        yield acquired
    finally:
        if acquired:
            lock_handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def sync_pilares_now(
    settings: Optional[Dict[str, Any]] = None,
    stats_path: str = STATS_FILE,
    lock_path: str = LOCK_FILE,
    post=requests.post,
) -> bool:
    """Envía el total actual de hasta tres meses y devuelve si n8n respondió 200."""
    try:
        settings = settings if settings is not None else _load_settings()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"[WARN] No se pudo cargar la configuración de sincronización: {error}")
        return False

    if not settings.get("pilares_sync_enabled", False):
        return False

    instance_id = settings.get("instance_id")
    if instance_id != EXPECTED_INSTANCE_ID:
        print(f"[WARN] Sincronización omitida: instance_id debe ser {EXPECTED_INSTANCE_ID!r}.")
        return False

    url = str(settings.get("pilares_sync_url", "")).strip()
    token = str(settings.get("pilares_sync_token", "")).strip()
    if not url or not token:
        print("[WARN] Sincronización omitida: faltan URL o token de Pilares.")
        return False

    timeout = _positive_number(settings.get("pilares_sync_timeout_seconds"), DEFAULT_TIMEOUT_SECONDS)
    with _try_sync_lock(lock_path) as acquired:
        if not acquired:
            print("[INFO] Sincronización omitida: otro proceso ya está sincronizando Pilares.")
            return False

        reports = get_latest_recorded_counts(limit=3, db_path=stats_path)
        if not reports:
            return True

        payload = {
            "schema_version": 1,
            "instance_id": EXPECTED_INSTANCE_ID,
            "reports": [
                {"period": period, "sent_count": sent_count}
                for period, sent_count in reports
            ],
        }
        try:
            response = post(
                url,
                json=payload,
                headers={"X-Pilares-Token": token},
                timeout=timeout,
            )
        except requests.RequestException as error:
            print(f"[WARN] Falló la sincronización con Pilares: {error}")
            return False

        if response.status_code == 200:
            print(f"[OK] Contador Pilares sincronizado ({len(reports)} período(s)).")
            return True

        print(f"[WARN] Pilares respondió HTTP {response.status_code}; se reintentará más tarde.")
        return False


def _periodic_sync_loop(stop_event: threading.Event) -> None:
    """Sincroniza inmediatamente y después en el intervalo configurado."""
    while not stop_event.is_set():
        sync_pilares_now()
        try:
            interval = get_sync_interval_seconds()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"[WARN] Intervalo de Pilares inválido; se usarán {DEFAULT_INTERVAL_SECONDS}s: {error}")
            interval = DEFAULT_INTERVAL_SECONDS
        stop_event.wait(interval)


def start_periodic_sync() -> threading.Event:
    """Inicia el hilo daemon de recuperación periódica y devuelve su señal de parada."""
    stop_event = threading.Event()
    threading.Thread(
        target=_periodic_sync_loop,
        args=(stop_event,),
        daemon=True,
        name="pilares-counter-sync",
    ).start()
    return stop_event

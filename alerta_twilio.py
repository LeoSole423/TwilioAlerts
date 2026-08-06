import os
import json
import requests
from datetime import datetime, timedelta, timezone
from PIL import Image, ExifTags
from twilio.rest import Client
import urllib3
from message_stats import record_message_sent
from pilares_sync import sync_pilares_now
from alert_filter import CameraLock, camera_from_filename, evaluate_alert, remember_sent_alert

urllib3.disable_warnings()  # Desactivar advertencias SSL

# -------------------- Configuración --------------------
BASE_DIR = os.path.dirname(__file__)
LOCAL_TZ = timezone(timedelta(hours=-3))  # UTC-3

# Cargar ajustes desde Settings.json (mismos que usa el código anterior)
settings_path = os.path.join(BASE_DIR, "Settings.json")
if not os.path.exists(settings_path):
    raise FileNotFoundError(f"No se encontró el archivo de configuración: {settings_path}")

with open(settings_path, "r", encoding="utf-8") as f:
    settings = json.load(f)

INSTANCE_NAME = settings.get("instance_name", "Nombre por defecto")
INSTANCE_ID = settings.get("instance_id", "ID por defecto")
IMAGE_FOLDER = settings.get("alerts_folder", "./alerts")
ALERTS_BASE_URL = settings.get("alerts_base_url")  # URL pública donde se sirven las imágenes

# Credenciales de Twilio y destinatarios (definidos en Settings.json)
ACCOUNT_SID = settings["twilio_account_sid"]
AUTH_TOKEN = settings["twilio_auth_token"]
CONTENT_SID = settings["twilio_content_sid"]  # Plantilla con botón Quick Reply
FROM_WHATSAPP = settings["twilio_from_whatsapp"]
RECIPIENTS = settings.get("recipients", [])

client = Client(ACCOUNT_SID, AUTH_TOKEN)

# Fichero que mantiene el estado por usuario
STATE_FILE = os.path.join(BASE_DIR, "user_state.json")

def load_state() -> dict:
    """Carga el estado persistente de los usuarios."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_state(state: dict):
    """Guarda el estado persistente."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] No se pudo guardar el estado: {e}")

# -------------------- Utilidades --------------------
TRANSLATIONS = {
    "person": "Persona",
    "vehicle": "Vehículo",
    "fire": "Fuego",
    "smoke": "Humo",
    "unknown": "Desconocido",
    "nothing found": "No se detectaron objetos",
    "no objects detected": "No se detectaron objetos",
}

def translate_label(label: str) -> str:
    return TRANSLATIONS.get(label.lower(), label)

def extract_label_confidence(image_path: str):
    """Extrae la etiqueta del EXIF (ImageDescription)."""
    try:
        with Image.open(image_path) as img:
            exif_data = img.getexif()
            if not exif_data:
                return "No se detectaron objetos", ""
            description = None
            for tag_id, val in exif_data.items():
                if ExifTags.TAGS.get(tag_id) == "ImageDescription":
                    description = val.decode("utf-8") if isinstance(val, bytes) else str(val)
                    break
            if not description:
                return "No se detectaron objetos", ""
            if ":" in description:
                label, confidence = description.split(":", 1)
                return label.strip(), confidence.strip()
            return description.strip(), "0%"
    except Exception as e:
        return f"Error: {e}", ""

# -------------------- Detección de la imagen más reciente --------------------
print(f"[DEBUG] Carpeta de alertas configurada: {IMAGE_FOLDER}")
if not os.path.isdir(IMAGE_FOLDER):
    raise NotADirectoryError(f"El directorio de alertas no existe: {IMAGE_FOLDER}")

jpg_files = [e for e in os.scandir(IMAGE_FOLDER) if e.is_file() and e.name.lower().endswith(".jpg")]
print(f"[DEBUG] Imágenes encontradas: {len(jpg_files)}")
if not jpg_files:
    raise FileNotFoundError(f"No se encontraron imágenes .jpg en {IMAGE_FOLDER}")

# Ordenar por fecha de modificación descendente
jpg_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
newest_entry = jpg_files[0]
image_path = os.path.join(IMAGE_FOLDER, newest_entry.name)
print(f"[DEBUG] Imagen seleccionada: {image_path}")

label, confidence = extract_label_confidence(image_path)
print(f"[DEBUG] Etiqueta detectada: {label} (confianza {confidence})")
label = translate_label(label)

# Timestamp del evento
event_ts = datetime.fromtimestamp(newest_entry.stat().st_mtime, tz=timezone.utc)
event_ts_local = event_ts.astimezone(LOCAL_TZ)
event_ts_seconds = newest_entry.stat().st_mtime
camera_name = camera_from_filename(newest_entry.name)

# -------------------- Lógica de envío --------------------
STATE = load_state()
now = datetime.now(timezone.utc)

TEMPLATE_COOLDOWN = timedelta(hours=settings.get("template_cooldown_hours", 1))
SESSION_DURATION = timedelta(hours=settings.get("session_duration_hours", 24))

def should_send_template(user_state: dict) -> bool:
    """Determina si debemos enviar la plantilla basándonos en la configuración."""
    last_template_str = user_state.get("last_template_sent")
    if not last_template_str:
        return True
    last_template = datetime.fromisoformat(last_template_str)
    return now - last_template >= TEMPLATE_COOLDOWN

def session_active(user_state: dict) -> bool:
    session_until_str = user_state.get("session_until")
    if not session_until_str:
        return False
    return datetime.fromisoformat(session_until_str) > now

def is_paused(user_state: dict, now_utc: datetime) -> bool:
    """Retorna True si el usuario tiene pausa vigente. Limpia pausas expiradas."""
    paused_until_str = user_state.get("paused_until")
    if paused_until_str:
        try:
            paused_until = datetime.fromisoformat(paused_until_str)
            if paused_until > now_utc:
                return True
            # Pausa expirada: limpiar banderas
            user_state.pop("paused", None)
            user_state.pop("paused_until", None)
            return False
        except Exception:
            pass
    return bool(user_state.get("paused"))

# Contadores para logging
sent_template = 0
sent_session = 0
skipped = 0
filtered = 0
sent_any_alert = False

filter_lock = None
filter_blocked = False
if settings.get("alert_filter_enabled", False) and camera_name:
    filter_lock = CameraLock(camera_name)
    if not filter_lock.acquire():
        filter_blocked = True
        filtered = 1
        print(f"[FILTER] Alerta descartada: {camera_name}, otra ejecución está procesando esta cámara.")
    else:
        decision = evaluate_alert(camera_name, newest_entry.name, event_ts_seconds, settings)
        if decision.would_filter:
            elapsed = "" if decision.elapsed_seconds is None else f", transcurrieron {decision.elapsed_seconds:.1f}s"
            if decision.should_send:
                print(f"[FILTER] Log-only: {camera_name} habría sido descartada ({decision.reason}{elapsed}).")
            else:
                filter_blocked = True
                filtered = 1
                print(f"[FILTER] Alerta descartada: {camera_name} ({decision.reason}{elapsed}).")
elif settings.get("alert_filter_enabled", False):
    print("[FILTER] No se pudo identificar la cámara; se enviará la alerta sin filtrar.")

for dest in RECIPIENTS if not filter_blocked else []:
    user_state = STATE.get(dest, {})
    # Auto-despausar si la pausa expiró
    paused_until_str = user_state.get("paused_until")
    if paused_until_str:
        try:
            if datetime.fromisoformat(paused_until_str) <= now:
                user_state.pop("paused", None)
                user_state.pop("paused_until", None)
                STATE[dest] = user_state
        except Exception:
            pass

    # Si el destinatario tiene pausa vigente, no enviar nada
    if is_paused(user_state, now):
        skipped += 1
        print(f"[SKIP] {dest} tiene alertas pausadas. No se envía mensaje.")
        continue
    if session_active(user_state):
        # Enviar mensaje de sesión (económico)
        body = (
            f"🔔 Alerta de movimiento en {INSTANCE_NAME}\n"
            f"🗓 Fecha y Hora: {event_ts_local.strftime('%Y-%m-%d %H:%M')} UTC-3\n"
            f"🔍 Objetos detectados: {label}"
        )
        media_param = {}
        if ALERTS_BASE_URL:
            filename = os.path.basename(image_path)
            media_url = f"{ALERTS_BASE_URL.rstrip('/')}/{filename}"
            media_param = {"media_url": [media_url]}
            print(f"[DEBUG] media_url asignado: {media_url}")
        else:
            print("[WARN] No se definió 'alerts_base_url' en Settings.json; el mensaje se enviará sin imagen.")
        try:
            print(f"[DEBUG] Enviando mensaje de sesión a {dest}")
            client.messages.create(
                from_=FROM_WHATSAPP,
                body=body,
                to=dest,
                **media_param,
            )
            sent_any_alert = True
            if record_message_sent():
                sync_pilares_now()
            sent_session += 1
            # Actualizar estado
            user_state["last_event_sent"] = now.isoformat()
            STATE[dest] = user_state
            print(f"[OK] Mensaje de sesión enviado a {dest}")
        except Exception as e:
            print(f"[ERR] Falló envío de sesión a {dest}: {e}")
    else:
        if should_send_template(user_state):
            variables = {
                "1": INSTANCE_NAME,
                "2": f"{event_ts_local.strftime('%Y-%m-%d %H:%M')} UTC-3",
                "3": label,
            }
            try:
                client.messages.create(
                    from_=FROM_WHATSAPP,
                    content_sid=CONTENT_SID,
                    content_variables=json.dumps(variables),
                    to=dest,
                )
                sent_any_alert = True
                if record_message_sent():
                    sync_pilares_now()
                sent_template += 1
                # Guardar timestamp de la plantilla
                user_state["last_template_sent"] = now.isoformat()
                STATE[dest] = user_state
                print(f"[OK] Plantilla enviada a {dest}")
            except Exception as e:
                print(f"[ERR] Falló envío de plantilla a {dest}: {e}")
        else:
            skipped += 1
            print(f"[SKIP] Se omitió envío a {dest}: plantilla enviada hace menos de {TEMPLATE_COOLDOWN} h y sin sesión activa.")

# Persistir el filtro solo tras un envío confirmado por Twilio.
if sent_any_alert and filter_lock is not None:
    remember_sent_alert(camera_name, newest_entry.name, event_ts_seconds, settings)
if filter_lock is not None:
    filter_lock.release()

# Guardar estado actualizado
save_state(STATE)

print(
    f"Resumen -> Plantillas: {sent_template}, Sesión: {sent_session}, Omitidos: {skipped}, Filtrados: {filtered}"
)

from flask import Flask, request, abort
from datetime import datetime, timedelta, timezone
import os
import json
from twilio.rest import Client
from PIL import Image, ExifTags
import threading

# -------------------- Config --------------------
BASE_DIR = os.path.dirname(__file__)
STATE_FILE = os.path.join(BASE_DIR, "user_state.json")

# Cargar configuraciones
settings_path = os.path.join(BASE_DIR, "Settings.json")
if not os.path.exists(settings_path):
    raise FileNotFoundError(f"No se encontró Settings.json en {BASE_DIR}")

with open(settings_path, "r", encoding="utf-8") as f:
    settings = json.load(f)

SESSION_DURATION = timedelta(hours=settings.get("session_duration_hours", 24))

app = Flask(__name__)

# -------------------- Cliente Twilio --------------------
ACCOUNT_SID = settings["twilio_account_sid"]
AUTH_TOKEN = settings["twilio_auth_token"]
FROM_WHATSAPP = settings["twilio_from_whatsapp"]
ALERTS_FOLDER = settings.get("alerts_folder", "./alerts")
ALERTS_BASE_URL = settings.get("alerts_base_url")

client = Client(ACCOUNT_SID, AUTH_TOKEN)

# -------------------- Utilidades de imagen --------------------
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
    """Extrae la etiqueta y confianza del EXIF ImageDescription."""
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


def send_last_alert(to_number: str):
    """Envía al usuario la alerta más reciente con imagen."""
    if not os.path.isdir(ALERTS_FOLDER):
        print(f"[WARN] Carpeta de alertas no encontrada: {ALERTS_FOLDER}")
        return

    jpg_files = [e for e in os.scandir(ALERTS_FOLDER) if e.is_file() and e.name.lower().endswith(".jpg")]
    if not jpg_files:
        print("[WARN] No hay imágenes .jpg en la carpeta de alertas; no se enviará imagen.")
        return

    # Seleccionamos la imagen más reciente
    jpg_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    newest_entry = jpg_files[0]
    image_path = os.path.join(ALERTS_FOLDER, newest_entry.name)

    label, _ = extract_label_confidence(image_path)
    label = translate_label(label)

    event_ts = datetime.fromtimestamp(newest_entry.stat().st_mtime, tz=timezone.utc)

    body = (
        f"🔔 Alerta de movimiento en {settings.get('instance_name', 'Instancia')}\n"
        f"🗓 Fecha y Hora: {event_ts.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"🔍 Objetos detectados: {label}"
    )

    media_param = {}
    if ALERTS_BASE_URL:
        filename = os.path.basename(image_path)
        base = ALERTS_BASE_URL.rstrip('/')
        if not base.startswith("http://") and not base.startswith("https://"):
            base = "https://" + base
        media_param = {"media_url": [f"{base}/{filename}"]}

    try:
        print(f"[DEBUG] Enviando alerta inmediata a {to_number}")
        client.messages.create(
            from_=FROM_WHATSAPP,
            body=body,
            to=to_number,
            **media_param,
        )
        print(f"[OK] Alerta enviada a {to_number}")
    except Exception as e:
        print(f"[ERR] No se pudo enviar la alerta a {to_number}: {e}")


def send_last_alert_async(to_number: str):
    """Envía la última alerta en un hilo separado para no bloquear la respuesta HTTP."""

    threading.Thread(target=send_last_alert, args=(to_number,), daemon=True).start()


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] No se pudo guardar el estado: {e}")


@app.route("/webhook", methods=["POST"])
def webhook():
    """Endpoint que Twilio llamará para los mensajes entrantes."""
    # --- Logging de la solicitud entrante ---
    try:
        print(
            f"[REQ] {datetime.now(timezone.utc).isoformat()} - IP {request.remote_addr} - Params: {dict(request.values)}"
        )
    except Exception as e:
        print(f"[WARN] No se pudo registrar la solicitud: {e}")

    from_number = request.values.get("From")
    if not from_number:
        abort(400)

    # ------- Filtro de remitentes permitidos -------
    allowed_senders = set(settings.get("recipients", []))
    if allowed_senders and from_number not in allowed_senders:
        # Descartamos silenciosamente (respondemos 200 para que Twilio no reintente)
        print(f"[INFO] Mensaje descartado de {from_number}: no está en 'recipients'.")
        return ("<Response></Response>", 200, {"Content-Type": "application/xml"})

    # Marcamos la sesión activa por 24 h a partir de ahora
    state = load_state()
    user_state = state.get(from_number, {})
    user_state["session_until"] = (datetime.now(timezone.utc) + SESSION_DURATION).isoformat()
    state[from_number] = user_state
    save_state(state)

    # Enviar alerta inmediata con imagen en segundo plano
    send_last_alert_async(from_number)

    # Respondemos con un mensaje de cortesía opcional
    return ("<Response><Message>Recibido. Enviaremos alertas por las próximas 24h."  # noqa: E501
            "</Message></Response>", 200, {"Content-Type": "application/xml"})


# -------------------- Hook global de logging --------------------
@app.before_request
def log_any_request():
    """Registra todos los intentos de conexión entrantes (GET, POST, etc.)."""
    try:
        print(
            f"[ANY] {datetime.now(timezone.utc).isoformat()} - {request.method} {request.path} - "
            f"IP {request.remote_addr} - Args: {dict(request.args)} - Form: {dict(request.form)}"
        )
    except Exception as e:
        print(f"[WARN] No se pudo registrar la petición genérica: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.get("webhook_port", 5000)))
    app.run(host="0.0.0.0", port=port) 

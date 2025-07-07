from flask import Flask, request, abort
from datetime import datetime, timedelta, timezone
import os
import json

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

    # Respondemos con un mensaje de cortesía opcional
    return ("<Response><Message>Recibido. Enviaremos alertas por las próximas 24h."  # noqa: E501
            "</Message></Response>", 200, {"Content-Type": "application/xml"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.get("webhook_port", 5000)))
    app.run(host="0.0.0.0", port=port) 
# Twilio Alerts

Este proyecto permite enviar alertas automáticas por WhatsApp usando Twilio, a partir de imágenes almacenadas en una carpeta local. Incluye:

- **alerta_twilio.py**: Envía la alerta con la imagen más reciente y su información.
- **twilio_webhook.py**: Webhook Flask para activar sesiones de alerta desde mensajes entrantes de WhatsApp.
- **carpeta_server.py**: Servidor HTTP simple para exponer la carpeta de alertas por red local.

## Requisitos

- Python 3.8+
- Cuenta de Twilio con acceso a WhatsApp

Instala las dependencias con:

```bash
pip install -r requirements.txt
```

## Configuración

Edita el archivo `Settings.json` con tus datos:

```json
{
  "instance_name": "Pilares",
  "instance_id": "Pilares",
  "alerts_folder": "D:/Alerts",
  "alerts_base_url": "http://TU_IP_LOCAL:8880",
  "twilio_account_sid": "<tu_sid>",
  "twilio_auth_token": "<tu_token>",
  "twilio_content_sid": "<tu_content_sid>",
  "twilio_from_whatsapp": "whatsapp:+14155238886",
  "recipients": ["whatsapp:+54911..."],
  "session_duration_hours": 24,
  "template_cooldown_hours": 1,
  "webhook_port": 5004,
  "static_server_port": 8880
}
```

- `alerts_folder`: Carpeta donde se guardan las imágenes de alerta.
- `alerts_base_url`: URL pública o local donde se exponen las imágenes (debe coincidir con el servidor de archivos).
- `twilio_*`: Credenciales y configuración de Twilio.
- `recipients`: Lista de destinatarios autorizados.
- `session_duration_hours`: Duración de la sesión de alertas tras recibir un mensaje.
- `template_cooldown_hours`: Tiempo mínimo entre plantillas enviadas.
- `webhook_port`: Puerto para el webhook Flask.
- `static_server_port`: Puerto para el servidor de archivos.

## Uso

### 1. Servir la carpeta de alertas

```bash
python carpeta_server.py
```
Esto expondrá la carpeta configurada en `alerts_folder` en la red local, en el puerto definido.

### 2. Webhook para WhatsApp

```bash
python twilio_webhook.py
```
Esto inicia un servidor Flask que responde a Twilio para activar sesiones de alerta.

### 3. Enviar alerta manualmente

```bash
python alerta_twilio.py
```
Esto enviará la imagen más reciente de la carpeta de alertas a los destinatarios configurados.

## Notas

- Asegúrate de que la URL de `alerts_base_url` sea accesible desde internet si Twilio debe acceder a las imágenes.
- El archivo `user_state.json` guarda el estado de las sesiones y se crea automáticamente.
- Puedes personalizar los textos y traducciones en el script `alerta_twilio.py`.

## Licencia

MIT 
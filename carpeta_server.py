import http.server
import socketserver
import os
import json

# Ruta completa de la carpeta que querés exponer
with open('Settings.json', 'r', encoding='utf-8') as f:
    settings = json.load(f)
directorio = settings.get('alerts_folder', r"D:\Alerts")

# Puerto en el que se expondrá el servidor
puerto = 8880

# Cambiamos el directorio actual al que querés exponer
os.chdir(directorio)

# Usamos el manejador HTTP simple de Python
handler = http.server.SimpleHTTPRequestHandler

# Iniciamos el servidor
with socketserver.TCPServer(("", puerto), handler) as httpd:
    print(f"✅ Servidor activo en http://localhost:{puerto}/")
    print(f"📂 Sirviendo archivos desde: {directorio}")
    httpd.serve_forever()
import http.server
import socketserver
import os

# Ruta completa de la carpeta que querés exponer
directorio = r"D:\Alerts"

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
"""Prueba URLs comunes de stream MJPEG/snapshot en una camara IP."""
import http.client, base64, sys, time

IP   = "192.168.1.26"
USER = "admin"
PASS = "admin"

token = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
auth  = f"Basic {token}"

CANDIDATES = [
    "/video.mjpg",
    "/mjpeg/video.mjpg",
    "/mjpg/video.cgi",
    "/videostream.cgi",
    "/cgi-bin/mjpg/video.cgi",
    "/cgi-bin/videostream.cgi",
    "/stream",
    "/streaming/channels/1",
    "/live/ch00_0",
    "/axis-cgi/mjpg/video.cgi",
    "/oneshotimage.jpg",           # ya sabemos que funciona
    "/image.jpg",
    "/snapshot.cgi",
]

print(f"Probando camara en {IP}  (usuario={USER})\n")

for path in CANDIDATES:
    try:
        conn = http.client.HTTPConnection(IP, timeout=3)
        conn.request("GET", path, headers={
            "Authorization": auth,
            "Connection": "close",
        })
        resp = conn.getresponse()
        ct   = resp.getheader("Content-Type", "")
        cl   = resp.getheader("Content-Length", "?")
        # Leer solo los primeros bytes para ver el tipo
        first = resp.read(256)
        conn.close()

        is_mjpeg    = "multipart" in ct.lower() or b"\xff\xd8" in first[:4]
        is_snapshot = ct.startswith("image/") or first[:2] == b"\xff\xd8"
        tag = ""
        if "multipart" in ct.lower():
            tag = "  <<< MJPEG STREAM"
        elif is_snapshot:
            tag = "  <<< snapshot JPEG"

        print(f"  {resp.status}  {path:40s}  Content-Type={ct[:40]}  len={cl}{tag}")
    except Exception as e:
        print(f"  ERR  {path:40s}  {e}")

print("\nListo.")

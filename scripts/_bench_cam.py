"""Mide FPS real y prueba URLs con resolucion reducida."""
import http.client, base64, time, numpy as np, cv2

IP   = "192.168.1.26"
USER = "admin"
PASS = "admin"
token = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
auth  = f"Basic {token}"

def bench(path, n=10):
    conn = http.client.HTTPConnection(IP, timeout=5)
    times = []
    size  = 0
    for i in range(n + 2):
        t0 = time.monotonic()
        conn.request("GET", path, headers={"Authorization": auth, "Connection": "keep-alive"})
        resp = conn.getresponse()
        jpg  = resp.read()
        dt   = time.monotonic() - t0
        if i >= 2:  # descartar warmup
            times.append(dt)
            size = len(jpg)
    conn.close()
    avg = sum(times) / len(times)
    fps = 1.0 / avg if avg > 0 else 0
    return fps, avg * 1000, size

print(f"Benchmarking camara {IP} con keep-alive\n")

# URL base
fps, ms, sz = bench("/oneshotimage.jpg")
print(f"  /oneshotimage.jpg          {fps:5.1f} fps  {ms:6.1f} ms/frame  {sz//1024} KB")

# Probar variantes de resolucion
for suffix in ["?resolution=320x240", "?resolution=640x480", "?size=2", "?size=1",
               "?quality=30", "?quality=50"]:
    try:
        fps2, ms2, sz2 = bench(f"/oneshotimage.jpg{suffix}", n=5)
        tag = " (mas rapido!)" if fps2 > fps + 1 else ""
        print(f"  /oneshotimage.jpg{suffix:20s}  {fps2:5.1f} fps  {ms2:6.1f} ms/frame  {sz2//1024} KB{tag}")
    except Exception as e:
        print(f"  /oneshotimage.jpg{suffix:20s}  ERROR: {e}")

print()
# Probar 2 conexiones en paralelo
import threading
results = []
def fetch():
    conn2 = http.client.HTTPConnection(IP, timeout=5)
    conn2.request("GET", "/oneshotimage.jpg", headers={"Authorization": auth})
    resp2 = conn2.getresponse()
    jpg2  = resp2.read()
    conn2.close()
    results.append(len(jpg2))

t0 = time.monotonic()
N = 10
for _ in range(N):
    t1 = threading.Thread(target=fetch)
    t2 = threading.Thread(target=fetch)
    t1.start(); t2.start()
    t1.join();  t2.join()
elapsed = time.monotonic() - t0
print(f"  2 conexiones paralelas x {N} pares:  {N*2/elapsed:.1f} fps efectivos")

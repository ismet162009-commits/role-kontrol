# Sureli acilan rolelerin suresini uzatir (kartin kendi zamanlayicisi en fazla ~54 dk).
# GitHub Actions bunu ~10 dakikada bir calistirir; telefon kapali olsa bile sure devam eder.
# Sadece Python standart kutuphanesi kullanir (kurulum gerekmez).
import base64, json, os, socket, ssl, struct, sys, time, random

HOST = os.environ.get("MQTT_HOST", "broker.hivemq.com")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
WS_PORT = int(os.environ.get("MQTT_WS_PORT", "8884"))     # telefonlarin da kullandigi sifreli web baglantisi
WS_YOL = os.environ.get("MQTT_WS_PATH", "/mqtt")
WS_TLS = os.environ.get("MQTT_WS_TLS", "1") != "0"
USER = os.environ.get("MQTT_USER", "")          # ileride flespi: anahtar buraya
KOD = os.environ.get("ROLE_KOD", "").strip()
N, KART_MAX_SN = 30, 3270

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

if not KOD:
    log("HATA: ROLE_KOD ayari (GitHub Secret) girilmemis."); sys.exit(1)

def crc16(b):
    c = 0xFFFF
    for x in b:
        c ^= x
        for _ in range(8): c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return bytes([c & 0xFF, c >> 8])
def cerceve(a): return bytes(a) + crc16(a)
def sureli(ch, sn):
    d = max(1, min(0x7FFF, round(sn * 10)))
    return cerceve([1, 5, 2, ch, d >> 8, d & 0xFF])
OKU = cerceve([1, 1, 0, 0, 0, N])

# ---------- cok kucuk MQTT 3.1.1 istemcisi ----------
def s(t): t = t.encode(); return struct.pack("!H", len(t)) + t
def uzunluk(n):
    o = b""
    while True:
        b, n = n % 128, n // 128
        o += bytes([b | (0x80 if n else 0)])
        if not n: return o
def paket(tip, govde): return bytes([tip]) + uzunluk(len(govde)) + govde

# ---------- baglanti: once web (8884), olmazsa duz TCP (1883) ----------
class TCP:
    ad = "tcp"
    def __init__(self):
        self.s = socket.create_connection((HOST, PORT), timeout=15)
    def gonder(self, b): self.s.sendall(b)
    def al(self, sure):
        self.s.settimeout(sure)
        try: v = self.s.recv(4096)
        except socket.timeout: return None
        if not v: raise ConnectionError("sunucu baglantiyi kapatti")
        return v
    def kapat(self):
        try: self.s.close()
        except Exception: pass

class WS(TCP):
    ad = "websocket"
    def __init__(self):
        ham = socket.create_connection((HOST, WS_PORT), timeout=15)
        self.s = ssl.create_default_context().wrap_socket(ham, server_hostname=HOST) if WS_TLS else ham
        anahtar = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {WS_YOL} HTTP/1.1\r\nHost: {HOST}:{WS_PORT}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {anahtar}\r\nSec-WebSocket-Version: 13\r\n"
                        "Sec-WebSocket-Protocol: mqtt\r\n\r\n").encode())
        cevap = b""
        while b"\r\n\r\n" not in cevap:
            v = self.s.recv(4096)
            if not v: raise ConnectionError("web baglantisi kapandi")
            cevap += v
        bas, self.ham = cevap.split(b"\r\n\r\n", 1)
        if b" 101 " not in bas.split(b"\r\n")[0]: raise ConnectionError("web baglantisi reddedildi: " + bas[:60].decode(errors="replace"))
    def gonder(self, b):
        maske = os.urandom(4)
        n = len(b)
        bas = bytes([0x82, 0x80 | n]) if n < 126 else bytes([0x82, 0x80 | 126]) + struct.pack("!H", n)
        self.s.sendall(bas + maske + bytes(x ^ maske[i % 4] for i, x in enumerate(b)))
    def al(self, sure):
        son = time.time() + sure
        while True:
            if len(self.ham) >= 2:
                n, i = self.ham[1] & 127, 2
                if n == 126: n, i = (struct.unpack("!H", self.ham[2:4])[0], 4) if len(self.ham) >= 4 else (None, 0)
                elif n == 127: n, i = (struct.unpack("!Q", self.ham[2:10])[0], 10) if len(self.ham) >= 10 else (None, 0)
                if n is not None and len(self.ham) >= i + n:
                    op, veri, self.ham = self.ham[0] & 15, self.ham[i:i + n], self.ham[i + n:]
                    if op == 8: raise ConnectionError("sunucu web baglantisini kapatti")
                    if op in (0, 1, 2): return veri
                    continue
            kalan = son - time.time()
            if kalan <= 0: return None
            v = TCP.al(self, kalan)
            if v is None: return None
            self.ham += v

def mqtt_baglan(t):
    bayrak = 0x02 | (0x80 if USER else 0)
    t.gonder(paket(0x10, s("MQTT") + bytes([4, bayrak]) + struct.pack("!H", 60)
                   + s("sure-uzat-" + "%06x" % random.getrandbits(24)) + (s(USER) if USER else b"")))

tampon = b""
def oku_paket(son):
    global tampon
    while True:
        if len(tampon) >= 2:
            n, carpan, i = 0, 1, 1
            while i < len(tampon):
                n += (tampon[i] & 127) * carpan; carpan *= 128
                if not tampon[i] & 128: break
                i += 1
            else:
                i = None
            if i is not None and len(tampon) >= i + 1 + n:
                p = tampon[:i + 1 + n]; tampon = tampon[i + 1 + n:]
                return p[0], p[i + 1:]
        kalan = son - time.time()
        if kalan <= 0: return None, None
        v = sok.al(kalan)
        if v is None: return None, None
        tampon += v

sok = None
for Tur in (WS, TCP):
    try:
        t = Tur(); tampon = b""
        mqtt_baglan(t); sok = t
        tip, g = oku_paket(time.time() + 10)
        if tip is not None and tip >> 4 == 2 and g[1] == 0:
            log(f"Sunucuya baglanildi ({Tur.ad})"); break
        log(f"{Tur.ad}: sunucu cevap vermedi", tip, g)
    except Exception as e:
        log(f"{Tur.ad}: baglanilamadi -", e)
    if sok: sok.kapat()
    sok = None
if sok is None:
    log("HATA: sunucuya hicbir yoldan baglanilamadi"); sys.exit(1)
konu = lambda k: KOD + "/" + k
sok.gonder(paket(0x82, struct.pack("!H", 1) + s(konu("sayaclar")) + b"\x00" + s(konu("cevap")) + b"\x00"))
def yayinla(k, veri, kalici=False):
    sok.gonder(paket(0x30 | (1 if kalici else 0), s(konu(k)) + veri))

# Mesajlari topla
sayaclar, coil = None, None
def mesajlari_al(sure, dur=lambda: False):
    global sayaclar, coil
    son = time.time() + sure
    while time.time() < son and not dur():
        tip, g = oku_paket(son)
        if tip is None: break
        if tip >> 4 != 3: continue
        tl = struct.unpack("!H", g[:2])[0]; k = g[2:2 + tl].decode(); veri = g[2 + tl + (2 if tip & 6 else 0):]
        if k == konu("sayaclar"):
            try: sayaclar = json.loads(veri.decode() or "{}")
            except Exception: sayaclar = {}
        elif k == konu("cevap") and len(veri) >= 7 and veri[1] == 1:
            coil = [bool((veri[3 + (i >> 3)] >> (i & 7)) & 1) for i in range(N)]

mesajlari_al(4, lambda: sayaclar is not None)
simdi = time.time() * 1000
aktif = {int(k): float(v) for k, v in (sayaclar or {}).items() if float(v) > simdi + 5000 and 0 <= int(k) < N}
if not aktif:
    log("Sureli acik role yok, yapacak is yok."); sok.kapat(); sys.exit(0)

yayinla("komut", OKU)
mesajlari_al(6, lambda: coil is not None)
if coil is None:
    log("Kart cevap vermiyor (cevrimdisi olabilir). Hicbir sey degistirilmedi."); sok.kapat(); sys.exit(0)

degisti = False
for ch, bitis in sorted(aktif.items()):
    kalan = (bitis - time.time() * 1000) / 1000
    if not coil[ch]:
        log(f"CH{ch+1}: role kapali (elle kapatildi ya da elektrik gitti) -> sure iptal")
        del sayaclar[str(ch)]; degisti = True
        continue
    parca = min(kalan, KART_MAX_SN)
    yayinla("komut", sureli(ch, parca))
    log(f"CH{ch+1}: kalan {kalan/60:.1f} dk -> kart {parca/60:.1f} dk icin yenilendi")
    time.sleep(0.3)

if degisti:
    yayinla("sayaclar", json.dumps(sayaclar).encode(), kalici=True)
mesajlari_al(2)
sok.gonder(b"\xe0\x00"); sok.kapat()
log("Bitti.")

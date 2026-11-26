"""E-Mükellef masaüstü programını (C:\\E-Mukellef\\E-Mukellef.exe) sürerek GİB'deki beyanname / tahakkukları E-Mükellef sitesine aktarır.

Neden: beyannameler E-Mükellef'e yalnız bu masaüstü programıyla aktarılıyor (sitede web işlemi yok, KESIF_NOTLARI.md). Program bir
PHP Desktop uygulaması: yerel web sunucusu + gömülü Chrome. Gömülü tarayıcının hata ayıklama (CDP) bağlantısıyla programın KENDİ
penceresi sürülür; kullanıcının elle yaptığı adımların aynısı yapılır (izlenen akış: KESIF_NOTLARI.md, 25.11.2026).

Kurallar:
- Hangi evrakın yükleneceğine PROGRAM karar verir: siteye daha önce yüklenmemiş olanları kendisi işaretler; işaretlere DOKUNULMAZ.
- "Evrakları Mükelleflere Gönder" (mail / SMS / bildirim) düğmesine ASLA basılmaz; yalnız IZINLI listesindeki düğmelere basılır.
- Giriş bilgisi yazılmaz: program kendi hatırladığı bilgilerle girer; alanlar boşsa durulur (kullanıcı programda bir kez girer).
- Beklenmeyen ekranda durulur, pencere olduğu gibi kullanıcıya bırakılır. Alan değerleri ve GİB oturum anahtarı günlüğe yazılmaz.
- Yükleme başladıktan sonra durdurulamaz (programın kendi işlemi); iptal yalnız yükleme başlamadan önce etkilidir.
Yalnız standart kitaplık kullanır.
"""
import base64
import json
import os
import re
import socket
import struct
import subprocess
import time
import urllib.request
from urllib.parse import urlparse

SUREC = "E-Mukellef"
# Basılmasına izin verilen düğmeler (yazının başı). Listede olmayan hiçbir şeye basılmaz.
IZINLI = ("Giriş Yap", "Beyanname Ara", "Seçili Evrakları Yükle", "SGK Evraklarını Ara", "Bu Adımı Atla",
          "Şimdi Evrakları Bilgisayarıma", "GİB'den Çıkış Yap")
YASAK = re.compile(r"mükellef\w*\s+gönder|gönder|yazdır|mail|sms|bildirim", re.I)
ILERLEME = re.compile(r"lütfen bekleyin|yükleniyor|kontrol ediyoruz|%\s*\d*\s*tamamlandı", re.I)
TOPLAM = re.compile(r"Toplam\s+(\d+)\s+Evrak\s+Listelendi", re.I)


class AktarHatasi(Exception):
    pass


class Iptal(Exception):
    pass


# ------------------------------------------------------------------ CDP (yalnız standart kitaplıkla küçük WebSocket istemcisi)
class _Cdp:
    def __init__(self, ws_adresi):
        u = urlparse(ws_adresi)
        if u.hostname not in ("127.0.0.1", "localhost"):
            raise AktarHatasi("Programın hata ayıklama adresi bu bilgisayarı göstermiyor; bağlanılmadı.")
        self.s = socket.create_connection((u.hostname, u.port), timeout=15)
        anahtar = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {anahtar}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        bas = b""
        while b"\r\n\r\n" not in bas:
            p = self.s.recv(1)
            if not p:
                raise AktarHatasi("Programın penceresine bağlanılamadı.")
            bas += p
        if b" 101 " not in bas.split(b"\r\n")[0]:
            raise AktarHatasi("Programın penceresine bağlanılamadı (başka bir araç bağlı olabilir).")
        self.sira = 0

    def kapat(self):
        try:
            self.s.close()
        except OSError:
            pass

    def _tam(self, n):
        b = b""
        while len(b) < n:
            p = self.s.recv(n - len(b))
            if not p:
                raise AktarHatasi("E-Mükellef programıyla bağlantı koptu (program kapanmış olabilir).")
            b += p
        return b

    def _al(self):
        """Bir metin iletisi (dict); denetim çerçeveleri (ping / kapat) atlanır."""
        while True:
            govde, tur = b"", None
            while True:
                b1, b2 = self._tam(2)
                if tur is None:
                    tur = b1 & 0x0F
                n = b2 & 0x7F
                if n == 126:
                    n = struct.unpack(">H", self._tam(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", self._tam(8))[0]
                govde += self._tam(n)
                if b1 & 0x80:
                    break
            if tur == 8:
                raise AktarHatasi("E-Mükellef programı bağlantıyı kapattı.")
            if tur == 1:
                return json.loads(govde.decode("utf-8", "replace"))

    def cagir(self, yontem, sure=30, **parametreler):
        self.sira += 1
        no = self.sira
        veri, maske = json.dumps({"id": no, "method": yontem, "params": parametreler}).encode(), os.urandom(4)
        n = len(veri)
        bas = b"\x81" + (bytes([0x80 | n]) if n < 126 else b"\xfe" + struct.pack(">H", n) if n < 65536 else b"\xff" + struct.pack(">Q", n))
        self.s.sendall(bas + maske + bytes(b ^ maske[i % 4] for i, b in enumerate(veri)))
        self.s.settimeout(sure)
        try:
            while True:
                m = self._al()
                if m.get("id") == no:
                    if "error" in m:
                        raise AktarHatasi(f"Program komutu yanıtlamadı ({yontem}).")
                    return m.get("result", {})
        except socket.timeout:
            raise AktarHatasi("E-Mükellef programı yanıt vermedi (pencere meşgul ya da kapanmış olabilir).")

    def js(self, ifade):
        """Sayfada ifadeyi çalıştırır; JSON metni dönen ifadenin değerini (dict/list) verir."""
        r = self.cagir("Runtime.evaluate", expression=ifade, returnByValue=True)
        if r.get("wasThrown") or r.get("exceptionDetails"):
            raise AktarHatasi("Programın sayfası beklenen yapıda değil (sayfa içi hata).")
        v = r.get("result", {}).get("value")
        return json.loads(v) if isinstance(v, str) else v


def _sayfa_bul():
    """Çalışan E-Mükellef programının penceresi (CDP hedefi); bulunamazsa AktarHatasi."""
    try:
        komut = ("Get-Process '" + SUREC + "' -ErrorAction SilentlyContinue | ForEach-Object { Get-NetTCPConnection -State Listen "
                 "-OwningProcess $_.Id -ErrorAction SilentlyContinue } | Where-Object { $_.LocalAddress -eq '127.0.0.1' } "
                 "| ForEach-Object { $_.LocalPort }")
        c = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", komut],
                           capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        raise AktarHatasi("E-Mükellef programı aranamadı (PowerShell çalıştırılamadı).")
    portlar = sorted({int(x) for x in c.stdout.split() if x.isdigit()})
    if not portlar:
        raise AktarHatasi("E-Mükellef masaüstü programı açık değil. Programı açıp yeniden deneyin.")
    adaylar = []
    for p in portlar:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/json/list", timeout=4) as y:
                liste = json.loads(y.read().decode("utf-8", "replace"))
        except Exception:
            continue                                 # uygulamanın kendi web portu
        if isinstance(liste, list):
            adaylar += [s for s in liste if isinstance(s, dict) and s.get("type") == "page"
                        and str(s.get("url", "")).startswith("http://127.0.0.1:")]
    if len(adaylar) != 1:
        raise AktarHatasi("E-Mükellef programının penceresi bulunamadı." if not adaylar
                          else "E-Mükellef programında birden çok pencere açık; fazlasını kapatıp yeniden deneyin.")
    if "webSocketDebuggerUrl" not in adaylar[0]:
        raise AktarHatasi("Programın penceresine başka bir araç bağlı; bağlanılamadı.")
    return adaylar[0]


# ------------------------------------------------------------------ sayfa içi yardımcılar (değer OKUNMAZ; yalnız dolu/boş)
_JS = r"""
const gor = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const yazi = e => String(e.innerText || e.value || '').replace(/\s+/g, ' ').trim();
const dugmeler = () => [...document.querySelectorAll('button, a.btn, input[type=submit], input[type=button]')]
    .filter(e => gor(e) && !e.disabled && !e.classList.contains('disabled'));
"""
_DURUM = "(() => {" + _JS + r"""
  const kutular = [...document.querySelectorAll('input[name="secili_evraklar[]"]')];
  const alanlar = {};
  [...document.querySelectorAll('form input')].filter(e => gor(e) && ['text', 'password', 'email'].includes(e.type))
      .forEach(e => { alanlar[e.name] = !!e.value; });
  return JSON.stringify({yol: location.pathname.replace('/index.php', '') + location.search, koken: location.origin,
      dugmeler: dugmeler().map(yazi).filter(Boolean),
      uyarilar: [...document.querySelectorAll('.alert, .modal.in, .sweet-alert, .swal2-popup')].filter(gor).map(e => yazi(e).slice(0, 400)).filter(Boolean),
      satir: kutular.length, isaretli: kutular.filter(k => k.checked).length, alanlar});
})()"""
_TIKLA = "((bas, tam) => {" + _JS + r"""
  const yasak = /mükellef\S*\s+gönder|gönder|yazdır|mail|sms|bildirim/i;
  const uyan = dugmeler().filter(e => { const y = yazi(e); return (tam ? y === bas : y.indexOf(bas) === 0) && !yasak.test(y); });
  if (!uyan.length) return JSON.stringify({ok: false});
  uyan[0].click();
  return JSON.stringify({ok: true, yazi: yazi(uyan[0]).slice(0, 60), adet: uyan.length});
})"""
_ISARETLILER = "(() => {" + _JS + r"""
  return JSON.stringify([...document.querySelectorAll('input[name="secili_evraklar[]"]')].filter(k => k.checked).slice(0, 300).map(k => {
      const h = [...k.closest('tr').children].map(td => yazi(td));       // Sıra | Ünvan | TC/Vergi No | Vergi Tür | Dönem
      return {unvan: h[2] || '', vkn: (h[3] || '').replace(/\D/g, ''), tur: h[4] || '', donem: h[5] || ''};
  }));
})()"""
_TARIH = "((bas, bit) => {" + _JS + r"""
  const yaz = (ad, deger) => {
      const e = document.querySelector('#beyanname-arama-form [name=' + ad + ']');
      if (!e) return false;
      e.value = deger;
      if (window.jQuery && jQuery.fn.datepicker) { try { jQuery(e).datepicker('update', deger); } catch (x) {} }
      e.value = deger;
      ['input', 'change'].forEach(o => e.dispatchEvent(new Event(o, {bubbles: true})));
      return e.value === deger;
  };
  return JSON.stringify({bas: yaz('tarih_baslangic', bas), bit: yaz('tarih_bitis', bit)});
})"""
_YUKLEME_MESAJLARI = "(() => {" + _JS + r"""
  return JSON.stringify([...document.querySelectorAll('.alert')].filter(gor).map(e => yazi(e).slice(0, 400))
      .filter(m => /—/.test(m) && !/İşlem Tamamlandı/.test(m)));
})()"""


class _Surucu:
    def __init__(self, gunluk, iptal):
        self.gunluk, self.iptal = gunluk, iptal or (lambda: False)
        self.cdp = _Cdp(_sayfa_bul()["webSocketDebuggerUrl"])
        self.yukleme_basladi = False

    def durum(self):
        d = self.cdp.js(_DURUM)
        if not isinstance(d, dict):
            raise AktarHatasi("Programın sayfası okunamadı.")
        return d

    def tikla(self, bas, yol, tam=False):
        """İzinli bir düğmeye, yalnız beklenen sayfadayken basar."""
        if not any(bas.startswith(i) for i in IZINLI) or YASAK.search(bas):
            raise AktarHatasi(f"İzin verilmeyen düğme: {bas}")
        d = self.durum()
        if yol not in d["yol"]:
            raise self.beklenmeyen(d, f"'{bas}' düğmesi için beklenen ekran açık değil")
        r = self.cdp.js(_TIKLA + f"({json.dumps(bas)}, {json.dumps(bool(tam))})")
        if not r or not r.get("ok"):
            raise self.beklenmeyen(d, f"'{bas}' düğmesi ekranda yok")

    def git(self, koken, yol):
        self.cdp.cagir("Page.navigate", url=koken + "/index.php/" + yol)

    def bekle(self, kosul, sure, ne, aralik=0.7):
        """kosul(durum) doğru bir değer dönene kadar bekler; iptal yalnız yükleme başlamadan önce etkilidir."""
        son, d, hata = time.time() + sure, None, 0
        while time.time() < son:
            if not self.yukleme_basladi and self.iptal():
                raise Iptal()
            try:
                yeni = self.durum()
                hata = 0
            except AktarHatasi:
                yeni = None                          # sayfa geçiş hâlinde olabilir
                hata += 1
                if hata >= 12:                       # art arda okunamıyor: program kapanmış / bağlantı kopmuş
                    raise
            if yeni is not None:
                d = yeni
                v = kosul(d)
                if v:
                    return v, d
            time.sleep(aralik)
        raise self.beklenmeyen(d, f"{ne} beklenirken süre doldu")

    @staticmethod
    def beklenmeyen(d, neden):
        ekran = f" (ekran: {d['yol']}; düğmeler: {', '.join(d['dugmeler'][:6]) or 'yok'}" + (
            f"; uyarı: {d['uyarilar'][0][:160]}" if d.get("uyarilar") else "") + ")" if d else ""
        return AktarHatasi(f"{neden}{ekran}. İşlem durduruldu; E-Mükellef programının penceresi olduğu gibi bırakıldı.")


def _hata_uyarisi(d, onceki=()):
    """İşlemden sonra çıkan, ilerleme / başarı bildirimi olmayan uyarı metni (varsa). onceki: işlemden önce ekranda olan uyarılar."""
    return next((u for u in d["uyarilar"] if u not in onceki and not ILERLEME.search(u) and not TOPLAM.search(u)
                 and not re.search(r"başarıyla|yönlendiriliyorsunuz", u, re.I)), "")


def _giris(s, bas_yol, hedef, sure, ad):
    """Programın hatırladığı bilgilerle 'Giriş Yap'a basar; hedef(durum) doğru olana kadar bekler -> durum."""
    onceki = tuple(s.durum()["uyarilar"])
    s.tikla("Giriş Yap", bas_yol, tam=True)
    try:
        return s.bekle(hedef, sure, ad)[1]
    except AktarHatasi:
        d = s.durum()
        raise AktarHatasi(f"{ad} yapılamadı: " + (_hata_uyarisi(d, onceki)[:200] or "program giriş ekranında kaldı")
                          + ". E-Mükellef programında elle giriş yapmayı deneyin.")


def _liste_bekle(s, sure, ne, onceki=()):
    """Arama listesinin TAMAMLANMASINI bekler -> ('liste', durum) | ('yok', durum). onceki: aramadan önce ekranda olan uyarılar."""
    sabit = {"metin": None, "an": 0.0}

    def kosul(d):
        if any(TOPLAM.search(u) for u in d["uyarilar"]) and any(x.startswith("Seçili Evrakları Yükle") for x in d["dugmeler"]):
            return "liste"
        u = _hata_uyarisi(d, onceki)                 # ör. "evrak bulunamadı": 4 sn boyunca değişmeyen, ilerleme olmayan YENİ uyarı
        if u and not any(ILERLEME.search(x) for x in d["uyarilar"]):
            if sabit["metin"] != u:
                sabit["metin"], sabit["an"] = u, time.time()
            elif time.time() - sabit["an"] >= 4:
                return "yok"
        else:
            sabit["metin"] = None
        return None
    return s.bekle(kosul, sure, ne)


def aktar(gunluk, tarih_bas, tarih_bit, sgk=True, yalniz_listele=False, iptal=None):
    """GİB'de tarih_bas–tarih_bit (gg.aa.yyyy, yükleme tarihi) aralığında verilmiş ve siteye henüz yüklenmemiş evrakları aktarır.
    Döner: {durum: 'yuklendi' | 'liste' | 'kayit_yok' | 'hata' | 'iptal', mesaj, bulunan, isaretli, evraklar: [...], yuklenen: [...]}."""
    b = {"durum": "hata", "mesaj": "", "bulunan": 0, "isaretli": 0, "evraklar": [], "evrak_ayrinti": [], "yuklenen": []}
    s = None
    try:
        s = _Surucu(gunluk, iptal)
        d = s.durum()
        koken = d["koken"]
        if re.search(r"/(beyanname_yukle|sgk_arama)", d["yol"]) and not any("İşlem Tamamlandı" in u for u in d["uyarilar"]):
            raise AktarHatasi("E-Mükellef programında süren bir yükleme var; bitince yeniden deneyin.")
        # 1) E-Mükellef (site) girişi: program bilgileri hatırlıyorsa kendi düğmesine basılır
        if "/site_giris" in d["yol"]:
            a = d["alanlar"]
            if not (a.get("site_adres") and a.get("mail_adres") and a.get("sifre")):
                raise AktarHatasi("E-Mükellef programında site giriş bilgileri kayıtlı değil. Programda bir kez elle giriş yapıp yeniden deneyin.")
            gunluk("  E-Mükellef programına giriş yapılıyor…")
            d = _giris(s, "/site_giris", lambda x: "/site_giris" not in x["yol"] and x["yol"] not in ("/", ""), 45, "E-Mükellef site girişi")
        # 2) GİB (e-Beyanname) girişi
        if "/beyanname_arama" not in d["yol"]:
            s.git(koken, "gib_giris")
            _, d = s.bekle(lambda x: "/gib_giris" in x["yol"] and "kullanici_kodu" in x["alanlar"] or "/beyanname_arama" in x["yol"],
                           30, "GİB giriş ekranı")
            if "/gib_giris" in d["yol"]:
                a = d["alanlar"]
                if not (a.get("kullanici_kodu") and a.get("parola") and a.get("sifre")):
                    raise AktarHatasi("E-Mükellef programında GİB giriş bilgileri kayıtlı değil. Programda 'Şifremi Hatırla' işaretli olarak "
                                      "bir kez elle giriş yapıp yeniden deneyin.")
                gunluk("  GİB e-Beyanname girişi yapılıyor…")
                d = _giris(s, "/gib_giris", lambda x: "/beyanname_arama" in x["yol"], 90, "GİB e-Beyanname girişi")
        s.bekle(lambda x: "/beyanname_arama" in x["yol"] and "Beyanname Ara" in x["dugmeler"], 30, "arama ekranı")
        # 3) Arama (yükleme tarihi aralığı); liste tamamlanana kadar beklenir
        t = s.cdp.js(_TARIH + f"({json.dumps(tarih_bas)}, {json.dumps(tarih_bit)})")
        if not t or not (t.get("bas") and t.get("bit")):
            raise s.beklenmeyen(s.durum(), "tarih alanları yazılamadı")
        gunluk(f"  GİB'de {tarih_bas} – {tarih_bit} arasında verilen beyannameler aranıyor…")
        onceki = tuple(s.durum()["uyarilar"])
        s.tikla("Beyanname Ara", "/beyanname_arama", tam=True)
        sonuc, d = _liste_bekle(s, 900, "arama sonucu", onceki)
        if sonuc == "yok":
            b.update(durum="kayit_yok", mesaj=_hata_uyarisi(d, onceki)[:300] or "Bu aralıkta evrak bulunamadı.")
        else:
            b["bulunan"], b["isaretli"] = d["satir"], d["isaretli"]
            b["evrak_ayrinti"] = [x for x in (s.cdp.js(_ISARETLILER) or []) if isinstance(x, dict)]
            b["evraklar"] = [" — ".join((x["unvan"], x["tur"], x["donem"])) for x in b["evrak_ayrinti"]]
            gunluk(f"  {b['bulunan']} evrak listelendi; siteye yüklenmemiş (program işaretledi): {b['isaretli']}.")
            for e in b["evraklar"][:40]:
                gunluk(f"    • {e}")
            if not b["isaretli"]:
                b.update(durum="kayit_yok", mesaj=f"{b['bulunan']} evrak bulundu; hepsi siteye daha önce yüklenmiş. Yüklenecek yeni evrak yok.")
            elif yalniz_listele:
                b.update(durum="liste", mesaj=f"{b['isaretli']} evrak yüklenmeyi bekliyor (yalnız listelendi; yükleme yapılmadı).")
        if b["durum"] in ("kayit_yok", "liste"):
            s.git(koken, "gib_guvenli_cikis")        # programın 'Güvenli Çıkış' bağlantısı: GİB oturumu açık bırakılmaz
            return b
        if s.iptal():
            raise Iptal()
        # 4) Yüklemeye geçiş; MUHSGK varsa araya SGK tahakkuk / hizmet belgesi ekranı girer
        s.tikla("Seçili Evrakları Yükle", "/beyanname_arama")
        yol_bitti = lambda x, ad: x["yol"].split("?")[0].rstrip("/").endswith("/" + ad)      # noqa: E731
        _, d = s.bekle(lambda x: yol_bitti(x, "sgk_arama") or yol_bitti(x, "beyanname_yukle"), 60, "yükleme ekranı")
        if yol_bitti(d, "sgk_arama"):
            s.bekle(lambda x: "Bu Adımı Atla" in x["dugmeler"], 30, "SGK ekranı")
            if sgk:
                gunluk("  MUHSGK için SGK tahakkuk / hizmet belgeleri aranıyor…")
                onceki = tuple(s.durum()["uyarilar"])
                s.tikla("SGK Evraklarını Ara", "/sgk_arama", tam=True)
                sonuc, d = _liste_bekle(s, 600, "SGK arama sonucu", onceki)
                if sonuc == "liste" and d["isaretli"]:
                    gunluk(f"  SGK: {d['satir']} evrak listelendi; yüklenecek: {d['isaretli']}.")
                    s.tikla("Seçili Evrakları Yükle", "/sgk_arama")
                elif "Bu Adımı Atla" in d["dugmeler"]:
                    gunluk("  SGK: yüklenecek yeni evrak yok; adım atlandı.")
                    s.tikla("Bu Adımı Atla", "/sgk_arama", tam=True)
                else:
                    raise s.beklenmeyen(d, "SGK ekranında yüklenecek evrak yok ve adımı atlama düğmesi görünmüyor")
            else:
                s.tikla("Bu Adımı Atla", "/sgk_arama", tam=True)
            s.bekle(lambda x: yol_bitti(x, "beyanname_yukle"), 60, "yükleme ekranı")
        s.bekle(lambda x: any(y.startswith("Şimdi Evrakları Bilgisayarıma") for y in x["dugmeler"]), 30, "yükleme düğmesi")
        if s.iptal():
            raise Iptal()
        # 5) Yükleme (programın kendi işlemi; başladıktan sonra durdurulmaz)
        s.yukleme_basladi = True
        gunluk("  Evraklar bilgisayara indiriliyor ve siteye yükleniyor… (bu sırada E-Mükellef programına dokunmayın)")
        s.tikla("Şimdi Evrakları Bilgisayarıma", "/beyanname_yukle")
        son_ilerleme = {"m": ""}

        def tamam_mi(x):
            i = next((re.search(r"Şuan\s+\d+\s*/\s*\d+\s+Evrak", u) for u in x["uyarilar"] if re.search(r"Şuan\s+\d+\s*/\s*\d+\s+Evrak", u)), None)
            if i and i.group(0) != son_ilerleme["m"]:
                son_ilerleme["m"] = i.group(0)
                gunluk("    " + i.group(0).replace("Şuan", "Şu an") + " yükleniyor…")
            return any("İşlem Tamamlandı" in u for u in x["uyarilar"])
        try:
            s.bekle(tamam_mi, 300 + 45 * max(b["isaretli"], 1) * 3, "yüklemenin bitmesi", aralik=1.5)
        finally:
            try:
                b["yuklenen"] = s.cdp.js(_YUKLEME_MESAJLARI) or []
            except AktarHatasi:
                pass
        for m in b["yuklenen"]:
            gunluk(f"    ✓ {m[:220]}")
        sorunlu = [m for m in b["yuklenen"] if "Yüklendi" not in m]
        b.update(durum="yuklendi", mesaj=f"{len(b['yuklenen'])} evrak işlendi" + (f"; {len(sorunlu)} evrakta 'yüklendi' bilgisi yok, "
                                                                                 "programın ekranından kontrol edin." if sorunlu else "."))
        # 6) Kullanıcının yaptığı gibi: GİB'den çık. "Evrakları Mükelleflere Gönder"e BASILMAZ.
        try:
            s.tikla("GİB'den Çıkış Yap", "/beyanname_yukle")
        except AktarHatasi:
            gunluk("  Not: GİB çıkış düğmesi bulunamadı; programda 'Güvenli Çıkış' yapın.")
        return b
    except Iptal:
        b.update(durum="iptal", mesaj="İşlem yükleme başlamadan durduruldu; hiçbir evrak yüklenmedi.")
        try:
            if s is not None:
                d = s.durum()
                if "/site_giris" not in d["yol"]:
                    s.git(d["koken"], "gib_guvenli_cikis")
        except Exception:
            pass
        return b
    except AktarHatasi as e:
        b["mesaj"] = str(e) + (" Yükleme başlamıştı: sonucu E-Mükellef programının ekranından kontrol edin; işlem yeniden başlatılmadı."
                               if s is not None and s.yukleme_basladi else "")
        return b
    finally:
        if s is not None:
            s.cdp.kapat()

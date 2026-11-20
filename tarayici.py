"""Playwright işçisi: tarayıcıyı ve tüm sorguları TEK bir iş parçacığında çalıştırır.

Arayüz (Tkinter) bu sınıfa yalnız görev gönderir (gonder) ve olayları kuyruktan okur; işçi Tkinter'a dokunmaz.
Olaylar: ("log", metin) ("durum", {emukellef, luca, gorunur}) ("calisiyor", bool) ("klasor", yol) ("ayrinti", metin)
         ("sorgu_durum", {...}) ("mukellefler", liste) ("bitti", {...}) ("gorev_bitti", {sira, gorev})
         ("giris_formu", {captcha, mesaj}) ("giris_kod", {mesaj}) ("giris_sonuc", {durum, mesaj})
         ("luca_giris_sonuc", {durum, mesaj}) ("luca_giris_dogrulama", {resim, mesaj}) ("luca_firmalar", [(sirket_id, ad)])

Tarayıcı varsayılan olarak ARKA PLANDA (penceresiz) çalışır; giriş gerekirse arayüz kendi giriş penceresini gösterir.

Luca: aynı tarayıcıda ayrı pencere. Luca sorguları (luca_sorgu.py) salt okunurdur; Luca oturumu E-Mükellef'ten bağımsızdır
(biri düşerse diğerinin sorguları çalışmaya devam eder). Ayrıntı: LUCA_NOTLARI.md.
"""
import os
import queue
import re
import threading
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

import birikim
import cikti
import dokum
import emukellef
import luca
import luca_sorgu
import ozet

PROFIL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KdvOnHazirlik" / "chrome-profil"

# Fatura sorguları: anahtar -> (yön, tür)
FATURA_SORGULARI = {"efatura_gelen": ("gelen", "efatura"), "efatura_giden": ("giden", "efatura"),
                    "earsiv": ("gelen", "earsiv"), "earsiv_giden": ("giden", "earsiv")}

# (anahtar, arayüz adı) — çalışma sırası
SORGULAR = [
    ("beyan", "Önceki dönem KDV1/KDV2 beyanname ve tahakkuk"),
    ("muhsgk", "MUHSGK beyanname ve tahakkuk"),
    ("sgk6661", emukellef.SGK6661),
    ("efatura_gelen", "Gelen E-Faturalar"),
    ("efatura_giden", "Giden E-Faturalar"),
    ("earsiv", "Gelen E-Arşiv Faturalar"),
    ("earsiv_giden", "Giden E-Arşiv Faturalar"),
    ("adina", "E-Arşiv Mükellef Adına Düzenlenenler"),
    ("iptal_itiraz", "E-Arşiv Portal İptal/İtiraz Talepleri"),
    ("kdv2", "KDV 2 Ödeme Sorgulama"),
    ("s109", "109 - Sorumlu Sıfatıyla Ödenen KDV"),
    ("alindi", "Ödeme / Alındı (tüm vergiler)"),
    ("tahsilat", "GİB Tahsilat Bilgileri"),
    ("borc", "Vergi Borcu"),
    ("okc", "Yeni Nesil ÖKC Aylık Satış Rapor Bildirimleri"),
    ("pos", "Toplu POS Sorgulama"),
    ("todeb", "TÖDEB Bilgileri"),
    ("ithalat", "İthalde Ödenen KDV"),
    ("arac", "Araç Bilgisi (mevcut)"),
    ("arac_gecmis", "Araç Bilgisi (geçmiş)"),
    ("arac_alim", "Araç Alım/Satım Bilgileri"),
    ("evrak", "GİB Gelen Evraklar"),
    ("bildirim", "GİB Bildirimlerim"),
    ("karsit", "Gelen Karşıt İnceleme Tutanakları"),
    ("etebligat", "E-Tebligatlar"),
] + luca_sorgu.SORGULAR

LUCA_SORGULARI = luca_sorgu.ANAHTARLAR

# 'GİB'den yeniden sorgula' seçeneğinin etkilediği sorgular
GIB_GUNCELLENEN = set(FATURA_SORGULARI) | {"adina", "etebligat"}


def chrome_yolu():
    """Kurulu Google Chrome'un yolu (Playwright'ın 'chrome' kanalının baktığı yerler); bulunamazsa None."""
    for ortam in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        kok = os.environ.get(ortam)
        if kok:
            yol = Path(kok) / "Google" / "Chrome" / "Application" / "chrome.exe"
            try:
                if yol.is_file():
                    return yol
            except OSError:
                continue
    return None


class Iptal(Exception):
    pass


class LucaOturumYok(Exception):
    """Luca penceresi/oturumu yok ya da çalışma sırasında düştü."""


def hizmet(anahtar):
    return "luca" if anahtar in LUCA_SORGULARI else "emukellef"


BIRIM = {"beyan": "belge", "muhsgk": "belge", "sgk6661": "aylık kayıt", "efatura_gelen": "fatura", "efatura_giden": "fatura", "earsiv": "fatura",
         "earsiv_giden": "fatura", "adina": "kayıt", "iptal_itiraz": "talep", "kdv2": "ödeme", "s109": "ödeme", "alindi": "ödeme",
         "tahsilat": "satır", "borc": "borç satırı", "okc": "bildirim", "pos": "satır", "todeb": "satır", "ithalat": "kayıt",
         "arac": "araç", "arac_gecmis": "araç", "arac_alim": "kayıt", "evrak": "evrak", "bildirim": "bildirim",
         "karsit": "tutanak", "etebligat": "tebligat", **luca_sorgu.BIRIM}


def kisa_sonuc(anahtar, s):
    """Arayüzde sorgunun yanında görünen kısa sonuç: '41 fatura', 'kayıt yok', 'hata'…"""
    d = s["durum"]
    if d != "veri":
        return {"kayit_yok": "kayıt yok", "hata": "hata", "calistirilmadi": "çalıştırılmadı"}.get(d, d)
    if anahtar in ("pos", "todeb") and isinstance(s.get("toplam"), (int, float)):
        return f"{s['toplam']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    n = len(s.get("satirlar") or [])
    return f"{n} {BIRIM.get(anahtar, 'kayıt')}" if n else "veri var"


def acil_metni(s):
    """Sonuç mesajındaki ACİL uyarısı (yoksa '')."""
    m = s.get("mesaj") or ""
    i = m.find("ACİL")
    return m[i:i + 300] if i >= 0 else ""


class Isci:
    FORM = "#giris_form_load"                       # E-Mükellef giriş formunun yüklendiği kutu

    def __init__(self, olay_kuyrugu: queue.Queue):
        self.olaylar = olay_kuyrugu
        self.gorevler = queue.Queue()
        self.iptal_istendi = threading.Event()
        self.ctx = None
        self.gorunur = False                        # tarayıcı penceresi görünür mü (varsayılan: arka planda)
        self.oturum = None                          # son bilinen E-Mükellef giriş durumu
        self.luca = None                            # son bilinen Luca oturumu (None: bakılmadı)
        self.luca_liste_alindi = False
        self.liste_alindi = False
        self.acilis_hatasi = None                   # Chrome açılamadıysa son hata (aynı hata günlüğe bir kez yazılır)
        self.son = None                             # son çalıştırmanın durumu ("yalnız hatalıları tekrar dene" için)
        self.sira = 0                               # gönderilen görev sayacı (arayüz iş parçacığında artar)
        self.iptal_sira = 0                         # bu sıraya kadar gönderilen görevler iptal edildi
        self.thread = threading.Thread(target=self._calis, name="playwright-isci", daemon=True)
        self.thread.start()

    # ------------------------------------------------------------ arayüzden çağrılanlar
    def gonder(self, gorev, **kw):
        self.sira += 1
        self.gorevler.put((self.sira, gorev, kw))

    def iptal(self):
        self.iptal_sira = self.sira                 # şu ana kadar gönderilen (kuyrukta bekleyen dahil) her görev
        self.iptal_istendi.set()

    def kapat(self):
        self.iptal()
        self.gonder("kapat")

    # ------------------------------------------------------------ işçi içi
    def _olay(self, tur, veri=None):
        self.olaylar.put((tur, veri))

    def _log(self, metin):
        self._olay("log", metin)

    def _iptal_mi(self):
        return self.iptal_istendi.is_set()

    def _calis(self):
        try:
            with sync_playwright() as p:
                self.p = p
                while True:
                    sira, gorev, kw = self.gorevler.get()
                    if gorev == "kapat":
                        break
                    sessiz = bool(kw.get("sessiz"))         # arka plan denetimi: düğmeleri kilitlemez, iptali silmez
                    iptalli = sira <= self.iptal_sira       # görev alınmadan önce Durdur'a basılmış
                    if not sessiz:
                        if not iptalli:
                            self.iptal_istendi.clear()
                        self._olay("calisiyor", True)
                    try:
                        if iptalli and gorev in ("baslat", "tekrar", "verilen_beyanname"):
                            self._log("İşlem başlamadan durduruldu.")
                        else:
                            getattr(self, "_g_" + gorev)(**kw)
                    except Iptal:
                        self._log("İşlem iptal edildi.")
                    except Exception as e:
                        if gorev.startswith("giris_y") or gorev in ("giris_kod", "luca_giris"):   # giriş bilgisi içerebilir: ayrıntı yazılmaz
                            self._log(f"HATA ({gorev}): {type(e).__name__}")
                        else:
                            self._log(f"HATA ({gorev}): {str(e).splitlines()[0]}")
                            self._olay("ayrinti", traceback.format_exc())
                    finally:
                        if not sessiz:
                            self._olay("gorev_bitti", {"sira": sira, "gorev": gorev})   # hangi görevin bittiği (komut kanalı için)
                            self._olay("calisiyor", False)
                self._tarayici_kapat()
        except Exception as e:
            self._log(f"HATA: tarayıcı motoru başlatılamadı: {e}")
            self._olay("calisiyor", False)

    def _tarayici_kapat(self):
        ctx, self.ctx = self.ctx, None
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass

    def _tarayici_baslat(self, gorunur):
        """Chrome'u kalıcı profille açar. Varsayılan arka planda (penceresiz); gorunur=True elle giriş/sorun giderme içindir."""
        PROFIL.mkdir(parents=True, exist_ok=True)
        try:
            if gorunur:
                ctx = self.p.chromium.launch_persistent_context(
                    str(PROFIL), channel="chrome", headless=False, no_viewport=True,
                    accept_downloads=False, args=["--start-maximized"])
            else:
                ctx = self.p.chromium.launch_persistent_context(
                    str(PROFIL), channel="chrome", headless=True, viewport={"width": 1280, "height": 900},
                    accept_downloads=False)
        except Exception as e:
            if chrome_yolu() is None:               # başka bilgisayarda en olası neden: Chrome kurulu değil
                raise RuntimeError("Google Chrome bu bilgisayarda kurulu değil (ya da bulunamadı). Chrome'u kurup programı yeniden açın.")
            raise RuntimeError(f"Chrome açılamadı ({str(e).splitlines()[0]}). Programın başka bir kopyası açık olabilir.")

        def kapandi(*_):                            # yalnız GÜNCEL bağlam kapanırsa boşalt (yeniden açılışta eskisi karışmasın)
            if self.ctx is ctx:
                self.ctx = None
        ctx.on("close", kapandi)
        self.ctx, self.gorunur = ctx, gorunur

    def _acik_sayfalar(self):
        return [p for p in self.ctx.pages if not p.is_closed()] if self.ctx else []

    def _durum_bildir(self):
        self._olay("durum", {"emukellef": self.oturum, "luca": self.luca, "gorunur": self.gorunur})

    def _luca_kontrol(self):
        """Luca oturumu açık mı: uygulama penceresi var ve salt okunur dönem sorgusu cevap veriyor (oturumu da canlı tutar)."""
        try:
            if self.ctx is None or luca.luca_sayfasi(self.ctx) is None:
                return False
            return bool(luca.oturum_var_mi(self.ctx)[0])
        except Exception:
            return False

    def _luca_guncelle(self, sessiz=False):
        """Luca durumunu yeniden hesaplar; değiştiyse günlüğe yazar, ilk bağlanışta firma listesini arayüze gönderir."""
        yeni = self._luca_kontrol()
        if yeni != self.luca and (yeni or self.luca):          # None -> False (hiç girilmemiş) sessiz geçer
            self._log("Luca: bağlandı." if yeni else "Luca oturumu kapandı; Luca sorguları için yeniden giriş gerekir.")
        self.luca = yeni
        if not yeni:
            self.luca_liste_alindi = False          # yeniden girişte (başka hesap olabilir) firma listesi tazelenir
        if yeni and not self.luca_liste_alindi:
            try:
                self._olay("luca_firmalar", luca.firma_listesi(luca.luca_sayfasi(self.ctx)))
                self.luca_liste_alindi = True
            except Exception as e:
                if not sessiz:
                    self._log(f"UYARI: Luca firma listesi alınamadı ({str(e).splitlines()[0]}).")
        return yeni

    def _acik_mi(self):
        """Tarayıcıya gerçek bir çağrı yaparak açık olduğunu doğrular (kullanıcı pencereyi kapatmış olabilir)."""
        if self.ctx is None:
            return False
        try:
            self.ctx.cookies(emukellef.ADRES)
            if not self._acik_sayfalar():
                raise RuntimeError("açık sayfa yok")
            return True
        except Exception:
            self._tarayici_kapat()
            return False

    def _g_tarayici_ac(self, gorunur=False, hizmet_adi="emukellef"):
        acik = self._acik_mi()
        if acik and self.gorunur != gorunur:        # mod değişimi: aynı profil iki kez açılamaz, önce kapat
            if self.luca:
                self._log("Not: tarayıcı yeniden açıldığı için Luca oturumu kapanır; Luca sorgusu için yeniden giriş yapılır.")
            self._tarayici_kapat()
            acik = False
        if not acik:
            self._tarayici_baslat(gorunur)
            self._log("Tarayıcı penceresi açıldı." if gorunur else "Tarayıcı arka planda açıldı.")
        if gorunur and hizmet_adi == "luca":        # Luca'ya elle giriş: giriş sayfası ayrı sekmede açılır
            try:
                pg = luca._giris_sayfasi(self.ctx)
                if luca.portal_sayfasi(self.ctx) is None and luca.luca_sayfasi(self.ctx) is None:
                    pg.goto(luca.LUCA_GIRIS, timeout=60_000)
                pg.bring_to_front()
                self._log("Luca'ya açılan pencerede giriş yapıp 'LUCA MALİ MÜŞAVİR PAKETİ'ni açın; program Luca'yı kendiliğinden algılar.")
            except Exception as e:
                self._log(f"UYARI: Luca giriş sayfası açılamadı ({str(e).splitlines()[0]}).")
        elif gorunur:
            sayfalar = self._acik_sayfalar()
            emk = next((p for p in sayfalar if p.url.startswith(emukellef.ADRES)), None)
            if emk is None:
                emk = next((p for p in sayfalar if p.url in ("about:blank", "")), None) or self.ctx.new_page()
                try:
                    emk.goto(emukellef.YONETIM + "dashboard", timeout=60_000)
                except Exception as e:
                    self._log(f"UYARI: E-Mükellef açılamadı ({str(e).splitlines()[0]}). Adresi pencerede elle açın.")
            try:
                emk.bring_to_front()
            except Exception:
                pass
        self._g_giris_kontrol()

    def _g_giris_kontrol(self, sessiz=False):
        if not self._acik_mi():
            try:
                self._tarayici_baslat(False)        # kapanmışsa arka planda yeniden aç
                if not sessiz:
                    self._log("Tarayıcı arka planda açıldı.")
            except Exception as e:
                self.oturum = self.luca = None
                self.gorunur = False
                self._durum_bildir()
                if not sessiz or str(e) != self.acilis_hatasi:      # arka plan denetiminde de ilk hata günlüğe yazılır
                    self._log(f"HATA: {e}")
                self.acilis_hatasi = str(e)
                return
        self.acilis_hatasi = None
        emk = emukellef.oturum_var_mi(self.ctx)
        degisti = emk != self.oturum
        self.oturum = emk
        self._luca_guncelle(sessiz=sessiz)
        self._durum_bildir()
        if not sessiz or degisti:
            self._log(f"E-Mükellef: {'giriş yapıldı' if emk else 'GİRİŞ GEREKLİ'}")
        if emk and (not sessiz or not self.liste_alindi):
            try:
                liste = emukellef.mukellef_listesi(self.ctx)
                self._olay("mukellefler", liste)
                self.liste_alindi = True
                if not sessiz:
                    self._log(f"Firma listesi güncellendi: {len(liste)} aktif mükellef.")
            except emukellef.OturumYok as e:
                self._log(f"HATA: {e}")
            except Exception as e:
                self._log(f"UYARI: firma listesi alınamadı ({str(e).splitlines()[0]}); kayıtlı liste kullanılıyor.")

    # ------------------------------------------------------------ program içinden giriş (tarayıcı arka plandayken)
    # Program captcha ÇÖZMEZ: resmi kullanıcıya gösterir, kullanıcının yazdığını forma yazar. Şifre/kodlar günlüğe yazılmaz.
    def _giris_sayfa(self):
        """E-Mükellef'in kendi sekmesi; yoksa boş sekme ya da yeni sekme (Luca pencerelerine dokunulmaz)."""
        s = self._acik_sayfalar()
        emk = next((p for p in s if p.url.startswith(emukellef.ADRES)), None)
        if emk is not None:
            return emk
        return next((p for p in s if p.url in ("about:blank", "")), None) or self.ctx.new_page()

    # ------------------------------------------------------------ Luca girişi (bilgileri kullanıcı programa girer)
    # Luca ek kod isterse (iki aşamalı doğrulama kapalıyken güvenlik kodu) program ÇÖZMEZ: görüntüyü kullanıcıya gösterir,
    # kullanıcının yazdığını forma yazar (kod=...). Robot doğrulaması isterse tarayıcıda elle giriş önerilir.
    def _luca_paketi_dene(self):
        """Önceki girişten kalan ortak ekran açıksa paketi yeniden açmayı dener (tek kısa deneme); olmazsa False."""
        try:
            self._log("Luca ortak ekranı açık; Luca Mali Müşavir Paketi yeniden açılıyor…")
            luca.uygulama_ac(self.ctx, deneme_sayisi=1, bekle_sn=20, gunluk=self._log)
            return self._luca_kontrol()
        except Exception:
            return False

    def _g_luca_giris(self, musteri_no, kullanici, parola, kod=None):
        gizli = (musteri_no, kullanici, parola, kod)
        asama = "tarayıcı"
        try:
            if not self._acik_mi():
                self._tarayici_baslat(False)
            if self._luca_kontrol():
                durum, mesaj = "tamam", ""
            elif kod is None and luca.portal_sayfasi(self.ctx) is not None and self._luca_paketi_dene():
                durum, mesaj = "tamam", ""          # ortak ekran hâlâ açıktı: yeniden giriş (ve kod) gerekmedi
            else:
                if kod is None:
                    asama = "giriş formu"
                    durum, mesaj = luca.giris_yap(self.ctx, musteri_no, kullanici, parola)
                else:
                    asama = "kod gönderme"
                    durum, mesaj = luca.giris_kod(self.ctx, musteri_no, kullanici, parola, kod)
                if durum == "dogrulama":            # kullanıcıdan kod beklenir
                    self._log("Luca girişi: Luca güvenlik/doğrulama kodu istiyor.")
                    self._olay("ayrinti", "Luca doğrulama sayfası yapısı (değer içermez): " + str(mesaj.get("yapi", "")))
                    self._olay("luca_giris_dogrulama", {"resim": mesaj["resim"], "mesaj": mesaj.get("mesaj", "")})
                    return
                if durum == "robot":
                    durum = "hata"
                if durum == "tamam":
                    asama = "Luca paketini açma (giriş kabul edildi)"
                    self._log("Luca girişi kabul edildi; Luca Mali Müşavir Paketi açılıyor…")
                    luca.uygulama_ac(self.ctx, gunluk=self._log)
            self._log("Luca girişi: " + {"tamam": "başarılı.", "hata": "kabul edilmedi.", "belirsiz": "sonuç anlaşılamadı."}[durum])
            if durum == "belirsiz":                 # tanı (giriş bilgisi içermez) günlük dosyasına
                self._olay("ayrinti", "Luca giriş tanısı: " + str(mesaj))
            self._luca_guncelle()
            if durum == "tamam" and not self.luca:
                durum, mesaj = "belirsiz", "Luca açıldı ama oturum doğrulanamadı. 'Tarayıcıda elle giriş' ile deneyin."
            self._durum_bildir()
            self._olay("luca_giris_sonuc", {"durum": durum, "mesaj": mesaj})
        except Exception as e:
            m = luca._ayikla(str(e).splitlines()[0] if str(e) else type(e).__name__, gizli)
            self._luca_guncelle(sessiz=True)
            self._durum_bildir()
            self._log(f"HATA: Luca girişi tamamlanamadı (aşama: {asama}): {m}")
            self._olay("luca_giris_sonuc", {"durum": "hata", "mesaj": f"Luca girişi yapılamadı (aşama: {asama}): {m}"})

    def _captcha_al(self, sayfa):
        sayfa.wait_for_selector("#captcha-image", state="visible", timeout=30_000)
        sayfa.wait_for_function("() => { const i = document.querySelector('#captcha-image'); return i && i.complete && i.naturalWidth > 0 }",
                                timeout=30_000)
        return sayfa.locator("#captcha-image").screenshot()

    def _g_giris_formu(self, mesaj=""):
        """Giriş sayfasını (yeniden) yükler ve captcha resmini arayüze gönderir."""
        try:
            if not self._acik_mi():
                self._tarayici_baslat(False)
            if emukellef.oturum_var_mi(self.ctx):
                self._olay("giris_sonuc", {"durum": "tamam"})
                self._g_giris_kontrol()
                return
            sayfa = self._giris_sayfa()
            sayfa.goto(emukellef.YONETIM + "giris", timeout=60_000)
            self._olay("giris_formu", {"captcha": self._captcha_al(sayfa), "mesaj": mesaj})
        except Exception as e:
            self._olay("giris_sonuc", {"durum": "hata", "mesaj": f"Giriş sayfası açılamadı: {str(e).splitlines()[0]}"})

    # Sayfa içi yardımcılar. Doldurulabilir alan: görünür, etkin, yazılabilir giriş (düğme / işaret kutusu değil).
    # Giriş formunun (1. adım) alanları: kul_adi, sifre, captcha. Bunların dışındaki doldurulabilir alan = doğrulama kodu kutusu adayı.
    _JS_YARDIMCI = """
        const gorunur = e => { const s = getComputedStyle(e);
            return s.display !== 'none' && s.visibility !== 'hidden' && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length); };
        const yazilir = i => !['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'image', 'reset'].includes(i.type)
            && !i.disabled && !i.readOnly && gorunur(i);
        const giris_alani = i => ['kul_adi', 'sifre', 'captcha'].includes(i.name);
    """
    _FORM_DURUMU = "(kutu) => {" + _JS_YARDIMCI + """
        const k = document.querySelector(kutu);
        if (!k) return {giris: false, kod: 0, uyari: '', hata: '', metin: ''};
        const girdiler = [...k.querySelectorAll('input')].filter(yazilir);
        const metin = s => [...k.querySelectorAll(s)].filter(gorunur).map(e => e.innerText.trim()).filter(Boolean).join(' ');
        return {giris: girdiler.some(i => i.name === 'sifre' || i.name === 'captcha'), kod: girdiler.filter(i => !giris_alani(i)).length,
                uyari: metin('.form-msg, .alert'), hata: metin('.form-msg.alert-danger, .alert-danger'),
                metin: k.innerText.replace(/\\s+/g, ' ').trim()};
    }"""
    _KOD_ADAYI = "(i) => {" + _JS_YARDIMCI + " return yazilir(i) && !giris_alani(i); }"
    _FORM_YAPISI = """(kutu) => {
        const k = document.querySelector(kutu);
        if (!k) return '';
        const f = k.querySelector('form');
        const yol = f && f.action ? new URL(f.action, location.href).pathname : '';
        return 'action=' + yol + '; ' + [...k.querySelectorAll('input, button, select, a')].map(e => e.tagName.toLowerCase() + ':' +
            (e.type || '') + ':' + (e.name || '') + ':' + (e.id || '') + (e.maxLength > 0 ? ':max' + e.maxLength : '')).join(', ');
    }"""

    @staticmethod
    def _yanit_coz(j):
        """Sitenin form eklentisinin okuduğu iki yanıt biçimi -> (başarı: True | False | 'no_action' | None (bilinmiyor), mesaj)."""
        if not isinstance(j, dict):
            return None, ""
        if "success" in j:
            b, mesaj = j.get("success"), emukellef._mesaj(j)
        else:                                       # eski biçim: {durum: 'success'|'error'|…, mesaj}
            b = {"success": True, "error": False}.get(j.get("durum"), j.get("durum"))
            mesaj = emukellef._html_metin(j.get("mesaj"))
        if b is False:
            return False, mesaj
        if b is True or (not isinstance(b, bool) and b in (1, "1")):
            return True, mesaj
        return ("no_action" if b == "no_action" else None), mesaj

    def _gonder_ve_bekle(self, sayfa, ilk):
        """Formu gönderir, sonucu bekler -> ('tamam'|'kod'|'hata'|'belirsiz', mesaj).
        Yanıt biçimi (sitenin kendi form eklentisi): {success: true|false|'no_action', data: {msg, func}}.
        DİKKAT: site BAŞARIDA da formun uyarı kutusuna mesaj yazar (ör. 'kod gönderildi', yeşil) ve doğrulama formunu ~200 ms sonra
        AYRI bir istekle aynı kutuya yükler. Bu yüzden uyarı metni tek başına hata sayılmaz: yalnız sunucunun 'false' yanıtı, kırmızı
        (alert-danger) uyarı ya da sonraki adımın yüklenememesi sorun sayılır; başarıda formun değişmesi beklenir."""
        onceki = sayfa.evaluate(self._FORM_DURUMU, self.FORM)
        adres = sayfa.evaluate("(kutu) => { const f = document.querySelector(kutu + ' form'); return f && f.action ? f.action.split('?')[0] : ''; }",
                               self.FORM)
        captcha_adresi = sayfa.evaluate("() => { const i = document.querySelector('#captcha-image'); return i ? i.src : ''; }")
        uyarilar, aksayan = [], []

        def formun(istek):                          # formun kendi gönderimi mi
            return istek.method == "POST" and (istek.url.split("?")[0] == adres if adres else "/ajax/" in istek.url)

        def dinle(d):                               # sitenin alert() pencereleri
            uyarilar.append(d.message)
            d.dismiss()

        def yanit(r):                               # sonraki adımın (ör. doğrulama formunun yüklenmesi) başarısız isteği
            if r.request.method == "POST" and r.status >= 400 and not formun(r.request):
                aksayan.append(f"HTTP {r.status}")

        def kopan(istek):
            if istek.method == "POST" and not formun(istek):
                aksayan.append("bağlantı hatası")
        sayfa.on("dialog", dinle)
        sayfa.on("response", yanit)
        sayfa.on("requestfailed", kopan)
        try:
            j, yanit_geldi = None, False
            try:
                with sayfa.expect_response(lambda r: formun(r.request), timeout=45_000) as bilgi:
                    sayfa.locator(f"{self.FORM} button[type=submit], {self.FORM} input[type=submit]").first.click()
                yanit_geldi = True
                try:
                    j = bilgi.value.json()
                except Exception:
                    j = None
            except Exception:
                pass                                # yanıt yakalanamadı: yine de sayfanın durumuna (kısa süre) bakılır
            basari, mesaj = self._yanit_coz(j)
            if basari is False:
                try:                                # site captcha'yı kendisi yeniler; yeni resim beklenir
                    sayfa.wait_for_function("(eski) => { const i = document.querySelector('#captcha-image');"
                                            " return !i || (i.src !== eski && i.complete && i.naturalWidth > 0); }", arg=captcha_adresi, timeout=3_000)
                except Exception:
                    pass
                return "hata", mesaj or "Giriş bilgileri kabul edilmedi."
            kabul = basari is True or basari == "no_action"
            son = time.time() + (45 if kabul else 30 if yanit_geldi else 12)
            while time.time() < son:
                sayfa.wait_for_timeout(400)
                try:
                    if not emukellef._giris_sayfasi_mi(sayfa.url):
                        if emukellef.oturum_var_mi(self.ctx, zaman_asimi=15_000):
                            return "tamam", ""
                        continue
                    d = sayfa.evaluate(self._FORM_DURUMU, self.FORM)
                except Exception:
                    continue                        # sayfa geçiş hâlinde
                if ilk and not d["giris"] and d["kod"]:         # giriş formu gitti, doğrulama kodu formu geldi
                    return "kod", d["uyari"] or mesaj or d["metin"][:300]
                if not ilk and d["giris"]:
                    return "hata", d["hata"] or d["uyari"] or "Doğrulama kabul edilmedi; giriş formuna dönüldü."
                if d["hata"] and d["hata"] != onceki["hata"]:
                    return "hata", d["hata"]
                if aksayan:
                    return "belirsiz", f"E-Mükellef'in sonraki adımı yüklenemedi ({aksayan[-1]}). Yeniden deneyin."
                if uyarilar and not kabul:
                    return "hata", uyarilar[-1]
            if emukellef.oturum_var_mi(self.ctx, zaman_asimi=15_000):
                return "tamam", ""
            if not yanit_geldi:
                return "belirsiz", "E-Mükellef yanıt vermedi."
            return "belirsiz", uyarilar[-1] if uyarilar else "Sonuç anlaşılamadı. 'Tarayıcıda elle giriş' ile deneyin."
        finally:
            for olay, isleyici in (("dialog", dinle), ("response", yanit), ("requestfailed", kopan)):
                try:
                    sayfa.remove_listener(olay, isleyici)
                except Exception:
                    pass

    def _giris_sonucu(self, sayfa, durum, mesaj):
        if durum == "tamam":
            self._olay("giris_sonuc", {"durum": "tamam"})
            self._g_giris_kontrol()
        elif durum == "kod":
            try:                                    # tanı için formun yapısı (değer içermez) ayrıntı günlüğüne
                self._olay("ayrinti", "E-Mükellef doğrulama formu yapısı (değer içermez): " + str(sayfa.evaluate(self._FORM_YAPISI, self.FORM))[:600])
            except Exception:
                pass
            self._olay("giris_kod", {"mesaj": mesaj})
        else:                                       # hata / belirsiz
            d = sayfa.evaluate(self._FORM_DURUMU, self.FORM)
            if d["kod"] and not d["giris"]:         # hâlâ doğrulama kodu adımında
                self._olay("giris_kod", {"mesaj": mesaj})
                return
            if durum == "hata" and d["giris"]:      # giriş formu duruyor; site captcha'yı yeniledi
                try:
                    self._olay("giris_formu", {"captcha": self._captcha_al(sayfa), "mesaj": mesaj})
                    return
                except Exception:
                    pass
            self._g_giris_formu(mesaj=mesaj)        # belirsiz ya da form bozuk: sayfa baştan yüklenir

    def _g_giris_yap(self, kul_adi, sifre, captcha):
        gizli = [x for x in (sifre, captcha) if x]
        try:
            if not self._acik_mi():
                self._tarayici_baslat(False)
            sayfa = self._giris_sayfa()
            if not emukellef._giris_sayfasi_mi(sayfa.url) or not sayfa.locator(f"{self.FORM} input[name=sifre]").count():
                self._g_giris_formu(mesaj="Giriş formu yenilendi; güvenlik kodunu yeniden yazın.")
                return
            sayfa.fill(f"{self.FORM} input[name=kul_adi]", kul_adi)
            sayfa.fill(f"{self.FORM} input[name=sifre]", sifre)
            sayfa.fill(f"{self.FORM} input[name=captcha]", captcha)
            durum, mesaj = self._gonder_ve_bekle(sayfa, ilk=True)
            self._log("E-Mükellef girişi: " + {"tamam": "başarılı.", "kod": "doğrulama kodu isteniyor.", "hata": "kabul edilmedi.",
                                              "belirsiz": "sonuç anlaşılamadı."}[durum])
            self._giris_sonucu(sayfa, durum, mesaj)
        except Exception as e:
            m = str(e).splitlines()[0] if str(e) else type(e).__name__
            for g in gizli:
                m = m.replace(g, "•••")
            self._olay("giris_sonuc", {"durum": "hata", "mesaj": f"Giriş yapılamadı: {m}"})

    def _g_giris_kod(self, kod):
        try:
            sayfa = self._giris_sayfa()
            kutular = ([el for el in sayfa.locator(f"{self.FORM} input").all() if el.evaluate(self._KOD_ADAYI)]
                       if emukellef._giris_sayfasi_mi(sayfa.url) else [])
            if not kutular:
                self._g_giris_formu(mesaj="Doğrulama adımı bulunamadı; girişi baştan yapın.")
                return
            if len(kutular) > 1 and len(kutular) == len(kod) and all((el.get_attribute("maxlength") or "") == "1" for el in kutular):
                for el, harf in zip(kutular, kod):  # her hane ayrı kutuda
                    el.fill(harf)
            else:                                   # birden fazla aday varsa adı koda benzeyen, yoksa ilki
                benzer = [el for el in kutular if re.search(r"kod|code|do[gğ]rula|otp|2fa|token|pin", " ".join(
                    el.get_attribute(a) or "" for a in ("name", "id", "placeholder")), re.I)]
                (benzer or kutular)[0].fill(kod)
            durum, mesaj = self._gonder_ve_bekle(sayfa, ilk=False)
            self._log("E-Mükellef doğrulama: " + {"tamam": "giriş yapıldı.", "kod": "kod yeniden isteniyor.", "hata": "kabul edilmedi.",
                                                 "belirsiz": "sonuç anlaşılamadı."}[durum])
            self._giris_sonucu(sayfa, durum, mesaj)
        except Exception as e:
            m = (str(e).splitlines()[0] if str(e) else type(e).__name__).replace(kod, "•••") if kod else type(e).__name__
            self._olay("giris_sonuc", {"durum": "hata", "mesaj": f"Doğrulama gönderilemedi: {m}"})

    # ------------------------------------------------------------ faturalar: tek tek indir + kaydet + döküm kaydı
    def _fatura_isleyici(self, f, ay, yil, yon, tur, secim, dokum_kayitlari, dosyalar):
        firma_vkn = f.get("vkn") or None
        pdf_iste = secim.get("pdf", False)
        uyari_say = [0]

        def uygun(bayt, fa, kesin=False):
            """XML bu belge mi: belge no + firmanın tarafı + (listede varsa) karşı tarafın VKN'si.
            GİB portal e-arşiv numaraları satıcıya göre baştan başladığından karşı VKN kontrolü şarttır.
            kesin=True (diskte bulunan eski dosya): XML'de karşı VKN boşsa da kabul edilmez."""
            ok, neden = dokum.xml_dogrula(bayt, fa["belge_no"], firma_vkn, yon)
            if not ok:
                return False, neden
            beklenen = re.sub(r"\D", "", str(fa["liste"].get("karsi_vkn") or ""))
            if beklenen:
                try:
                    x = dokum.xml_coz(bayt)
                except ValueError as e:
                    return False, str(e)
                gercek = x["satici_vkn"] if yon == "gelen" else x["alici_vkn"]
                if (gercek or kesin) and gercek != beklenen:
                    return False, f"karşı taraf VKN'si tutmuyor ({gercek} ≠ {beklenen})"
            return True, ""

        def isle(fa, indir=True):
            hedef = cikti.fatura_klasoru(f["klasor"], yil, ay, yon, tur, self._log)
            xml_yol = pdf_yol = None
            xml, xml_d = None, None
            adaylar = cikti.mevcut_faturalar(hedef, f["ad"], yil, ay, yon, tur, fa["belge_no"])
            for ax, ap in adaylar:
                if ax is None:
                    continue
                try:
                    b = ax.read_bytes()
                except OSError:
                    continue
                if uygun(b, fa, kesin=True)[0]:
                    xml_yol, pdf_yol, xml, xml_d = ax, ap, b, "vardı"
                    break
            if xml_yol is None:                     # yalnız PDF'i olan aday: dosya adındaki karşı taraf adı listedekiyle tutuyorsa kabul
                on_ek = cikti._fatura_on_ek(f["ad"], yil, ay, yon, tur, fa["belge_no"]) + " "
                karsi_ad = cikti.temiz_ad(fa["liste"].get("karsi") or "")
                for ax, ap in adaylar:
                    ek = ap.stem[len(on_ek):] if ap is not None and ax is None and ap.stem.startswith(on_ek) else ""
                    if karsi_ad and len(ek) >= min(12, len(karsi_ad)) and karsi_ad.startswith(ek):   # ad kısaltılmış olabilir
                        pdf_yol = ap
                        break
            if xml is None:
                if not indir:
                    xml_d = "iptal"
                elif not fa["id"]:
                    xml_d = "İNDİRİLEMEDİ"
                else:
                    xb = emukellef.fatura_belgesi(self.ctx, tur, fa["id"], "xml")
                    ok, neden = uygun(xb, fa) if xb else (False, "boş yanıt")
                    if ok:
                        xml, xml_d = xb, "indirildi"
                    else:
                        xml_d = "İNDİRİLEMEDİ"
                        uyari_say[0] += 1
                        if uyari_say[0] <= 5:
                            self._log(f"    UYARI: {fa['belge_no']} XML kontrolü geçmedi: {neden}")
                        elif uyari_say[0] == 6:
                            self._log("    UYARI: … diğer XML uyarıları gizlendi (sonuç dosyasında hepsi 'İNDİRİLEMEDİ' görünür).")
            pdf = None
            if pdf_yol:
                pdf_d = "vardı"
            elif not pdf_iste:
                pdf_d = "istenmedi"
            elif not indir:
                pdf_d = "iptal"
            elif not fa["id"]:
                pdf_d = "İNDİRİLEMEDİ"
            else:
                pdf = emukellef.fatura_belgesi(self.ctx, tur, fa["id"], "pdf")
                pdf_d = "indirildi" if pdf else "İNDİRİLEMEDİ"
            dosya = (xml_yol or pdf_yol).name if (xml_yol or pdf_yol) else ""
            mevcut = xml_yol or pdf_yol
            if mevcut is not None and (xml_d == "indirildi" or pdf is not None):
                # Belgenin bir parçası zaten kayıtlı: eksik parçayı AYNI adla yanına koy (satıcı adı farklı yazılsa da)
                for bayt, uz in ((xml if xml_d == "indirildi" else None, ".xml"), (pdf, ".pdf")):
                    hedef_yol = mevcut.with_suffix(uz)
                    if bayt is not None and not hedef_yol.exists():
                        dosyalar.append(cikti.guvenli_yaz(hedef_yol, bayt))
            elif xml_d == "indirildi" or pdf is not None:
                kayit = {"belge_no": fa["belge_no"], "karsi": fa["liste"].get("karsi"), "karsi_vkn": fa["liste"].get("karsi_vkn"),
                         "pdf": pdf, "xml": xml}
                yollar, _ = cikti.fatura_kaydet(f["ad"], f["klasor"], yil, ay, kayit, self._log, yon=yon, tur=tur)
                dosyalar.extend(yollar)
                if yollar:
                    dosya = yollar[0].name
            for d in (pdf_d, xml_d):
                sayac[d] = sayac.get(d, 0) + 1
            # Sonuç dosyasında "indirildi"/"vardı" ayrımı yapılmaz (yoksa her çalıştırmada içerik değişir); ikisi de "var"
            pdf_s, xml_s = ("var" if d in ("indirildi", "vardı") else d for d in (pdf_d, xml_d))
            dokum_kayitlari.append(dokum.kayit_olustur(yon, tur, fa["belge_no"], xml, fa["liste"], dosya,
                                                       durum=f"PDF {pdf_s}, XML {xml_s}"))
            return pdf_s, xml_s
        sayac = {}
        isle.sayac = sayac
        return isle

    # ------------------------------------------------------------ asıl iş
    def _luca_sorgu(self, anahtar, f, ay, yil, dosyalar):
        ad = luca_sorgu.AD[anahtar]
        sid = str(f.get("luca_sirket_id") or "").strip()
        if not sid:
            return emukellef.calistirilmadi(ad, "Bu firma için Luca firması tanımlı değil (Başlat'ta sorulur).")
        pg = luca.luca_sayfasi(self.ctx)
        if pg is None or pg.frame(name="frm4") is None:
            raise LucaOturumYok("Luca penceresi açık değil")
        try:
            s, dosya = luca_sorgu.calistir(anahtar, self.ctx, pg, sid, ay, yil, self._log, self._iptal_mi)
        except luca.Iptal:
            raise
        except luca.LucaHatasi as e:
            if not self._luca_kontrol():            # hata oturum kaybından mı: ayrıca doğrulanır
                raise LucaOturumYok(str(e))
            raise
        if dosya:
            yol, durum = cikti.luca_kaydet(f["ad"], f["klasor"], yil, ay, dosya["tur"], dosya["bayt"], dosya["bas"], dosya["bit"], self._log)
            dosyalar.append(yol)
            self._log(f"    {yol.name}: {'zaten vardı (aynı)' if durum == 'ayni' else 'kaydedildi'}")
            s["mesaj"] += f" Dosya: {yol.name}."
        return s

    def _sorgu_calistir(self, anahtar, f, ay, yil, secim, dosyalar, dokum_kayitlari=None):
        if anahtar in LUCA_SORGULARI:
            return self._luca_sorgu(anahtar, f, ay, yil, dosyalar)
        ctx, mid, iptal = self.ctx, f["emukellef_id"], self._iptal_mi
        if anahtar == "kdv2":
            return emukellef.kdv2_odeme(ctx, mid, ay, yil)
        if anahtar == "sgk6661":
            return emukellef.asgari_destek(ctx, mid, ay, yil, iptal=iptal)
        if anahtar == "s109":
            return emukellef.sorumlu_109(ctx, mid, ay, yil)
        if anahtar == "okc":
            return emukellef.okc(ctx, mid, ay, yil)
        if anahtar == "pos":
            return emukellef.pos(ctx, mid, ay, yil, iptal=iptal)
        if anahtar == "todeb":
            return emukellef.todeb(ctx, mid, ay, yil, iptal=iptal)
        if anahtar == "ithalat":
            return emukellef.ithalat(ctx, mid, ay, yil)
        if anahtar == "arac":
            return emukellef.arac(ctx, mid)
        if anahtar == "arac_gecmis":
            return emukellef.arac_gecmis(ctx, mid)
        if anahtar == "arac_alim":
            return emukellef.arac_alim_satim(ctx, mid, yil)
        if anahtar == "adina":
            return emukellef.adina_duzenlenen(ctx, mid, ay, yil, guncelle=secim["earsiv_gib"], iptal=iptal)
        if anahtar == "iptal_itiraz":
            return emukellef.iptal_itiraz(ctx, mid, ay, yil)
        if anahtar == "alindi":
            return emukellef.odeme_alindi(ctx, mid, ay, yil)
        if anahtar == "tahsilat":
            return emukellef.tahsilat(ctx, mid, ay, yil)
        if anahtar == "borc":
            return emukellef.vergi_borcu(ctx, mid)
        if anahtar == "evrak":
            return emukellef.gelen_evrak(ctx, mid)
        if anahtar == "bildirim":
            return emukellef.bildirimler(ctx, mid)
        if anahtar == "karsit":
            return emukellef.karsit_inceleme(ctx, mid)
        if anahtar == "etebligat":
            s, belgeler = emukellef.etebligat(ctx, mid, guncelle=secim["earsiv_gib"], iptal=iptal)
            say, pdfsiz = {}, []
            for b in belgeler:
                pdf = emukellef.imzali_pdf_cikar(b["bayt"])
                yollar, d = cikti.tebligat_kaydet(f["ad"], f["klasor"], b, pdf, self._log)
                dosyalar.extend(yollar)
                say[d] = say.get(d, 0) + 1
                if pdf is None:
                    pdfsiz.append(f"{b['zaman']:%d.%m.%Y} {b['aciklama']}")
                if d != "ayni":
                    self._log(f"    {yollar[-1].name}: {'PDF tamamlandı' if d == 'tamamlandi' else 'kaydedildi'}")
            if belgeler:
                self._log(f"    E-tebligat dosyaları: {say.get('yeni', 0)} yeni, {say.get('ayni', 0)} zaten vardı"
                          + (f", {say['tamamlandi']} PDF'i tamamlandı" if say.get("tamamlandi") else "") + ".")
            if pdfsiz:
                s["mesaj"] += (f" UYARI: {len(pdfsiz)} tebligatın PDF'i imzalı paketten çıkarılamadı (imzalı .imz dosyası kaydedildi, "
                               "e-imza görüntüleyiciyle açın): " + "; ".join(pdfsiz[:5]))
            return s
        if anahtar in ("beyan", "muhsgk"):
            fn = emukellef.beyanname_tahakkuk if anahtar == "beyan" else emukellef.muhsgk
            s, belgeler = fn(ctx, mid, ay, yil, iptal=iptal)
            yabanci = self._yabanci_vkn(f, belgeler)
            if yabanci:                             # başka mükellefin belgesi firma klasörüne yazılmaz
                kimlik = {id(b) for b in yabanci}
                belgeler = [b for b in belgeler if id(b) not in kimlik]
                s["durum"] = "hata"
                s["mesaj"] += (f" UYARI: {len(yabanci)} belge başka VKN'li ({', '.join(sorted({str(b['vkn']) for b in yabanci}))}) olduğu için "
                               "KAYDEDİLMEDİ; E-Mükellef'te elle kontrol edin.")
            cikti.beyanname_adlari(belgeler)          # asıl + düzeltme varsa düzeltmelere kalıcı ek
            for b in belgeler:
                yol, durum = cikti.beyanname_kaydet(f["ad"], f["klasor"], b, self._log)
                dosyalar.append(yol)
                self._log(f"    {yol.name}: {'zaten vardı (aynı)' if durum == 'ayni' else 'kaydedildi'}")
            return s
        if anahtar in FATURA_SORGULARI:
            yon, tur = FATURA_SORGULARI[anahtar]
            kayitlar = []
            isle = self._fatura_isleyici(f, ay, yil, yon, tur, secim, kayitlar, dosyalar)
            if tur == "earsiv":
                s = emukellef.e_arsiv(ctx, mid, ay, yil, yon=yon, guncelle=secim["earsiv_gib"], iptal=iptal, gunluk=self._log, isle=isle)
            else:
                s = emukellef.e_fatura(ctx, mid, ay, yil, yon=yon, guncelle=secim["earsiv_gib"], iptal=iptal, gunluk=self._log, isle=isle)
            if dokum_kayitlari is not None:
                onceki = dokum_kayitlari.get(anahtar) or []
                if s["durum"] != "hata" or len(kayitlar) >= len(onceki):
                    dokum_kayitlari[anahtar] = kayitlar       # tekrar denemede öncekinin yerine geçer
                # hatalı ve daha az kayıtlı tekrar, öncekinde alınmış faturaları dökümden silmez
            if kayitlar:
                say = isle.sayac
                self._log(f"    Dosyalar (PDF+XML): {say.get('indirildi', 0)} indirildi, {say.get('vardı', 0)} zaten vardı, "
                          f"{say.get('İNDİRİLEMEDİ', 0)} indirilemedi" + (f", {say.get('istenmedi', 0)} PDF istenmedi" if say.get('istenmedi') else "") + ".")
            return s
        raise ValueError(anahtar)

    def _ozet_yaz(self, f, ay, yil, secim, sonuclar, kayitlar):
        """Kontrol özeti MD: sorgu sonuçları + XML dökümü + önceki dönem beyanname PDF'leri + dosya listesi."""
        firma_k = cikti.firma_klasoru(f["klasor"])
        oy, oa = (yil, ay - 1) if ay > 1 else (yil - 1, 12)
        onceki_beyan = firma_k / "KDV" / f"{oy}-{oa:02d}" / "04_Beyannameler"          # klasör açılmaz, varsa okunur
        muhsgk_onceki = firma_k / "Beyannameler" / "MUHSGK" / f"{oy}-{oa:02d}"
        muhsgk_bu = firma_k / "Beyannameler" / "MUHSGK" / f"{yil}-{ay:02d}"
        ek = [("Önceki dönem beyannameleri", onceki_beyan), (f"MUHSGK {oa:02d}/{oy}", muhsgk_onceki),
              (f"MUHSGK {ay:02d}/{yil}", muhsgk_bu), ("E-Tebligat", firma_k / "E-Tebligat")]
        var = {s["sorgu"] for s in sonuclar}
        secilmeyen = [ad for k, ad in SORGULAR if ad not in var]      # dönemde hiç çalıştırılmamış sorgular
        metin = ozet.ozet_metni(f, f"{cikti.AYLAR[ay - 1]} {yil}", sonuclar, kayitlar, [onceki_beyan, muhsgk_onceki],
                                cikti.donem_klasoru(f["klasor"], yil, ay, gunluk=self._log), secilmeyen, ek)
        return cikti.ozet_kaydet(f["klasor"], yil, ay, f["ad"], metin, self._log)

    def _hazir_mi(self, anahtarlar):
        """Çalışacak sorguların hizmetlerine göre giriş denetimi -> {'emukellef': engel|None, 'luca': engel|None}.
        Gereken hizmetlerin HİÇBİRİNE giriş yoksa hata verir; biri eksikse yalnız onun sorguları çalıştırılmaz."""
        if not self._acik_mi():
            self._tarayici_baslat(False)
            self._log("Tarayıcı arka planda açıldı.")
        gerek = {hizmet(k) for k in anahtarlar}
        engel = {"emukellef": None, "luca": None}
        if "emukellef" in gerek:
            self.oturum = emukellef.oturum_var_mi(self.ctx)
            if not self.oturum:
                engel["emukellef"] = "E-Mükellef'e giriş yapılmamış"
        if "luca" in gerek and not self._luca_guncelle():
            engel["luca"] = "Luca'ya giriş yapılmamış"
        self._durum_bildir()
        if gerek and all(engel[h] for h in gerek):
            raise RuntimeError(" / ".join(engel[h] for h in sorted(gerek)) + ". Giriş yapıp tekrar Başlat'a basın.")
        for h in sorted(gerek):
            if engel[h]:
                self._log(f"UYARI: {engel[h]}; bu hizmetin sorguları çalıştırılmayacak.")
        return engel

    def _g_baslat(self, secim):
        f = secim["firma"]
        ay, yil = secim["ay"], secim["yil"]
        self._log(f"===== {f['ad']} — {cikti.AYLAR[ay - 1]} {yil} =====")
        cikti.firma_klasoru(f["klasor"])          # klasör yoksa en başta dur
        secilen = [(k, ad) for k, ad in SORGULAR if secim.get(k)]
        if any(hizmet(k) == "emukellef" for k, _ in secilen) and not f.get("emukellef_id"):
            raise RuntimeError(f"{f['ad']} için firmalar.json'da emukellef_id yok.")
        engel = self._hazir_mi([k for k, _ in secilen])
        self.son = None                             # yarım kalan önceki çalıştırma artık tekrar edilemez
        self._calistir_kaydet({"secim": secim, "secilen": secilen, "sonuclar": [None] * len(secilen), "deneme": [0] * len(secilen),
                               "dokum_kayitlari": {}, "dosyalar": [], "engel": engel}, list(range(len(secilen))))

    @staticmethod
    def _yabanci_vkn(f, belgeler):
        """Firmanın VKN/TCKN'siyle AYNI UZUNLUKTA ama farklı numara taşıyan belgeler (başka mükellefin belgesi). Firma numarası
        bilinmiyorsa ya da tür farklıysa (VKN 10, TCKN 11 hane) karşılaştırılamaz: o belgeler yabancı sayılmaz."""
        fv = re.sub(r"\D", "", str(f.get("vkn") or ""))
        return [b for b in belgeler if fv and b.get("vkn") and len(str(b["vkn"])) == len(fv) and str(b["vkn"]) != fv]

    def _g_verilen_beyanname(self, firma, ay, yil):
        """'Verilen beyannameyi indir' düğmesi: seçilen dönemin VERİLMİŞ KDV1/KDV2/MUHSGK beyanname + tahakkuklarını indirir.
        Başka sorgu çalıştırmaz; sonuç dosyası / Kontrol Özeti yazmaz. Sonuç ("beyanname_bitti", {...}) olayıyla arayüze gider."""
        f, donem = firma, f"{cikti.AYLAR[ay - 1]} {yil}"
        bilgi = {"firma": f["ad"], "donem": donem, "durum": "hata", "mesaj": "", "belgeler": [], "klasorler": [], "yeni": 0, "ayni": 0}
        self._log(f"===== {f['ad']} — {donem}: verilen beyanname indiriliyor =====")
        try:
            cikti.firma_klasoru(f["klasor"])        # klasör yoksa en başta dur
            if not f.get("emukellef_id"):
                raise RuntimeError(f"{f['ad']} için firmalar.json'da emukellef_id yok.")
            if not self._acik_mi():
                self._tarayici_baslat(False)
                self._log("Tarayıcı arka planda açıldı.")
            self.oturum = emukellef.oturum_var_mi(self.ctx)
            self._durum_bildir()
            if not self.oturum:
                raise RuntimeError("E-Mükellef'e giriş yapılmamış. Giriş yapıp düğmeye yeniden basın.")
            s, belgeler = emukellef.verilen_beyanname(self.ctx, f["emukellef_id"], ay, yil, iptal=self._iptal_mi)
            yabanci = self._yabanci_vkn(f, belgeler)
            if yabanci:                             # başka mükellefin belgesi geldiyse hiçbiri kaydedilmez
                raise RuntimeError(f"E-Mükellef başka VKN'li belge döndürdü ({', '.join(sorted({str(b['vkn']) for b in yabanci}))}; firmanın "
                                   f"numarası {f.get('vkn')}). Hiçbir belge kaydedilmedi; E-Mükellef'te elle kontrol edin.")
            cikti.beyanname_adlari(belgeler)        # asıl + düzeltme varsa düzeltmelere kalıcı ek
            for b in belgeler:
                yol, durum = cikti.beyanname_kaydet(f["ad"], f["klasor"], b, self._log)
                bilgi["ayni" if durum == "ayni" else "yeni"] += 1
                bilgi["belgeler"].append(f"{b['tur']} {b['belge']}" + (f" ({b['ek']})" if b.get("ek") else ""))
                if str(yol.parent) not in bilgi["klasorler"]:
                    bilgi["klasorler"].append(str(yol.parent))
                self._log(f"    {yol.name}: {'zaten vardı (aynı)' if durum == 'ayni' else 'kaydedildi'}")
            bilgi.update(durum=s["durum"], mesaj=s["mesaj"])
        except emukellef.OturumYok as e:
            self.oturum = False
            self._durum_bildir()
            bilgi["mesaj"] = str(e)
        except Exception as e:
            bilgi["mesaj"] = (str(e).splitlines()[0] if str(e) else type(e).__name__)[:400]
        if bilgi["durum"] == "veri":
            self._log(f"Verilen beyanname ({donem}): {len(bilgi['belgeler'])} belge — {bilgi['yeni']} yeni kaydedildi, {bilgi['ayni']} zaten vardı.")
        elif bilgi["durum"] == "kayit_yok":
            self._log(f"Verilen beyanname ({donem}): {bilgi['mesaj']}")
        else:
            self._log(f"HATA: verilen beyanname indirilemedi ({donem}): {bilgi['mesaj']}")
        self._olay("beyanname_bitti", bilgi)

    def _g_son_unut(self, sessiz=True):
        """Kayıt klasörü değişti: son çalıştırmanın 'yalnız hatalıları tekrar dene' durumu unutulur (eski sonuçlar yeni klasöre yazılmasın)."""
        self.son = None

    def _g_tekrar(self, firma_ek=None):
        """Son çalıştırmada hata veren / çalıştırılmayan sorguları yeniden çalıştırır; öncekilerle birleştirip dosyaları yeniden yazar.
        firma_ek: çalıştırmadan sonra tanımlanan firma bilgisi (ör. {'luca_sirket_id': ...}) eski seçime aktarılır."""
        d = self.son
        if not d:
            self._log("Tekrar denenecek bir çalıştırma yok; Başlat'a basın.")
            return
        if firma_ek:
            d["secim"]["firma"].update(firma_ek)
        hedef = [i for i, s in enumerate(d["sonuclar"]) if s["durum"] in ("hata", "calistirilmadi")]
        if not hedef and not d.get("yazma_hatalari"):
            self._log("Tekrar denenecek hatalı sorgu yok.")
            return
        f, ay, yil = d["secim"]["firma"], d["secim"]["ay"], d["secim"]["yil"]
        ne = f"{len(hedef)} sorgu tekrar deneniyor" if hedef else "dosya kaydı yeniden deneniyor"
        self._log(f"===== {f['ad']} — {cikti.AYLAR[ay - 1]} {yil}: {ne} =====")
        d["engel"] = self._hazir_mi([d["secilen"][i][0] for i in hedef]) if hedef else {"emukellef": None, "luca": None}
        for i in hedef:
            d["deneme"][i] = 0
        self._calistir_kaydet(d, hedef)

    def _calistir_kaydet(self, d, hedef):
        """d: çalıştırma durumu; hedef: çalıştırılacak sorgu indisleri. Sonunda TÜM sonuçlarla dosyalar yazılır."""
        secim, secilen, sonuclar, deneme = d["secim"], d["secilen"], d["sonuclar"], d["deneme"]
        dokum_kayitlari, dosyalar = d["dokum_kayitlari"], d["dosyalar"]
        f, ay, yil = secim["firma"], secim["ay"], secim["yil"]
        durdu = None                                # yalnız kullanıcı iptali: kalan her şeyi durdurur
        engel = d.get("engel") or {"emukellef": None, "luca": None}     # hizmet -> neden (giriş yok / oturum düştü)
        dusen = []                                  # çalışma sırasında düşen oturumlar (sonuç panelinde görünür)

        def bildir(i, durum):
            veri = {"anahtar": secilen[i][0], "durum": durum}
            s = sonuclar[i]
            if s is not None and durum == s["durum"]:
                veri.update({"kisa": kisa_sonuc(secilen[i][0], s), "mesaj": s["mesaj"], "kapsam": s["kapsam"], "acil": bool(acil_metni(s))})
            self._olay("sorgu_durum", veri)

        for i in hedef:                             # arayüzde çalışacak kutular "bekliyor" olur
            bildir(i, "bekliyor")

        def calistir(i):
            """i. sorguyu bir kez çalıştırır; sonuç sonuclar[i]'ye yazılır. Oturum düşerse durdurma nedeni döner."""
            anahtar, ad = secilen[i]
            deneme[i] += 1
            bildir(i, "calisiyor")
            self._log(("" if anahtar in LUCA_SORGULARI else "E-Mükellef: ") + f"{ad} …" + (f" (deneme {deneme[i]})" if deneme[i] > 1 else ""))
            try:
                s = self._sorgu_calistir(anahtar, f, ay, yil, secim, dosyalar, dokum_kayitlari)
                if anahtar in GIB_GUNCELLENEN and not secim.get("earsiv_gib") and s["durum"] != "calistirilmadi":
                    s["mesaj"] = "GİB'den yeniden sorgulanmadı (E-Mükellef'te kayıtlı liste; yeni belgeler eksik olabilir). " + s["mesaj"]
            except emukellef.OturumYok as e:
                self._log(f"HATA: {e}")
                deneme[i] -= 1                      # tamamlanmayan deneme sayılmaz
                if sonuclar[i] is None or sonuclar[i]["durum"] == "calistirilmadi":
                    sonuclar[i] = emukellef.calistirilmadi(ad, "E-Mükellef oturumu düştü")
                bildir(i, sonuclar[i]["durum"])
                self.oturum = False
                self._durum_bildir()
                engel["emukellef"] = "E-Mükellef oturumu düştü"     # yalnız E-Mükellef sorguları durur; Luca sürer
                dusen.append(engel["emukellef"])
                return None
            except LucaOturumYok as e:
                self._log(f"HATA: Luca oturumu düştü ({str(e).splitlines()[0][:150]}).")
                deneme[i] -= 1
                if sonuclar[i] is None or sonuclar[i]["durum"] == "calistirilmadi":
                    sonuclar[i] = emukellef.calistirilmadi(ad, "Luca oturumu düştü")
                bildir(i, sonuclar[i]["durum"])
                self.luca = False
                self._durum_bildir()
                engel["luca"] = "Luca oturumu düştü"
                dusen.append(engel["luca"])
                return None
            except luca.Iptal:
                deneme[i] -= 1
                if sonuclar[i] is None:
                    sonuclar[i] = emukellef.calistirilmadi(ad, "İptal edildi")
                bildir(i, sonuclar[i]["durum"])
                return "İptal edildi"
            except luca.LucaHatasi as e:            # Luca rapor/istek hatası: sonuç hata olur, otomatik tekrar denenir
                s = emukellef._sonuc(ad, "", "hata", f"Luca: {str(e).splitlines()[0]}")
            except Exception as e:                  # kayıt hatası vb.: sonucu hata olarak yaz, devam et
                s = emukellef._sonuc(ad, "", "hata", f"Kayıt/işlem hatası: {str(e).splitlines()[0]}")
                self._olay("ayrinti", traceback.format_exc())
            sonuclar[i] = s
            bildir(i, s["durum"])
            ozet = cikti.DURUM_ADI.get(s["durum"], s["durum"]).upper() if s["durum"] in ("veri", "hata") else cikti.DURUM_ADI.get(s["durum"], s["durum"])
            tutar = f" — toplam {s['toplam']:,.2f}" if isinstance(s["toplam"], (int, float)) else ""
            self._log(f"  {ad} ({s['kapsam']}): {ozet}{tutar}. {s['mesaj']}")
            return None

        for i in hedef:
            if durdu or self._iptal_mi():
                durdu = durdu or "İptal edildi"
            neden = durdu or engel[hizmet(secilen[i][0])]
            if neden:
                if sonuclar[i] is None:
                    sonuclar[i] = emukellef.calistirilmadi(secilen[i][1], neden)
                bildir(i, sonuclar[i]["durum"])
                continue
            durdu = calistir(i)

        # Hata veren sorguları otomatik tekrar dene (en fazla 2 tur: 10 sn, 30 sn sonra)
        for tur, bekle in enumerate((10, 30), start=2):
            hatali_i = [i for i in hedef if sonuclar[i] and sonuclar[i]["durum"] == "hata" and not engel[hizmet(secilen[i][0])]]
            if not hatali_i or durdu:
                break
            self._log(f"Hata veren {len(hatali_i)} sorgu {bekle} sn sonra tekrar denenecek ({tur}. deneme)…")
            for _ in range(bekle):
                if self._iptal_mi():
                    break
                time.sleep(1)
            for i in hatali_i:
                if self._iptal_mi():
                    durdu = "İptal edildi"
                    break
                if engel[hizmet(secilen[i][0])]:    # bu turda oturumu düşen hizmet yeniden denenmez
                    continue
                durdu = calistir(i)
                if durdu:
                    break
        for i in hedef:
            s = sonuclar[i]
            if s and deneme[i] > 1:
                s["mesaj"] += (f" ({deneme[i]}. denemede alındı)" if s["durum"] != "hata" else f" ({deneme[i]} denemede de hata)")
        for i, s in enumerate(sonuclar):
            if s is None:
                sonuclar[i] = emukellef.calistirilmadi(secilen[i][1], durdu or engel[hizmet(secilen[i][0])] or "Çalıştırılmadı")
            if i in hedef:
                bildir(i, sonuclar[i]["durum"])    # son durum (tekrarlar dahil) arayüzde kesinleşir
        durdu = durdu or (" / ".join(dusen) if dusen else None)      # panelde: iptal ya da çalışırken düşen oturum
        bitis = {"durdu": durdu, "ozet": None, "dokum": None, "donem_klasoru": None, "tebligat_klasoru": None}
        yazma = d["yazma_hatalari"] = []            # dosya kaydı hataları: sonuç panelinde görünür, tekrar denenebilir
        if sonuclar:
            try:
                yollar = cikti.emukellef_kaydet(f["ad"], f["klasor"], yil, ay, sonuclar,
                                                {"emukellef_id": f.get("emukellef_id"), "luca_sirket_id": f.get("luca_sirket_id")}, self._log)
                dosyalar[:0] = [y for y in yollar if y not in dosyalar]
                self._olay("klasor", str(yollar[0].parent))
            except Exception as e:
                yazma.append(("Sonuç dosyası yazılamadı", str(e).splitlines()[0]))
                self._log(f"HATA: sonuç dosyası yazılamadı: {str(e).splitlines()[0]}")
                self._olay("ayrinti", traceback.format_exc())
        kayitlar = [k for liste in dokum_kayitlari.values() for k in liste]
        calisan = {FATURA_SORGULARI[k] for k in dokum_kayitlari}
        # Birikim (kullanıcı kararı 19.11.2026): özet ve döküm, dönemin tüm çalıştırmalarındaki en güncel başarılı sonuçlarla kurulur.
        birlesik, kayit_tum, tur_tum = sonuclar, kayitlar, calisan
        try:
            birlesik = birikim.birlestir(sonuclar, birikim.onceki_sonuclar(f["klasor"], yil, ay, self._log), [ad for _, ad in SORGULAR])
            kayit_tum, tur_tum = birikim.dokum_kayitlari(f, f["klasor"], yil, ay, birlesik, kayitlar)
        except Exception as e:                      # birikim kurulamazsa yalnız bu çalıştırmanın sonuçlarıyla sürer
            birlesik, kayit_tum, tur_tum = sonuclar, kayitlar, calisan
            self._log(f"UYARI: önceki çalıştırmaların sonuçları birleştirilemedi ({str(e).splitlines()[0]}); özet yalnız bu çalıştırmayı içeriyor.")
            self._olay("ayrinti", traceback.format_exc())
        if tur_tum:                                 # fatura sorgusu bu çalıştırmada olmasa da döküm dönemin tamamıyla güncel tutulur
            try:
                bayt, icerik, notlar = dokum.excel(f["ad"], f"{cikti.AYLAR[ay - 1]} {yil}", kayit_tum, tur_tum)
                yol, sonuc = cikti.dokum_kaydet(f["ad"], f["klasor"], yil, ay, bayt, icerik, self._log)
                bitis["dokum"] = str(yol)
                self._log(f"Fatura dökümü ({len(kayit_tum)} fatura, dönemin tamamı): {yol.name}" + (" — zaten vardı" if sonuc == "ayni" else ""))
                for n in notlar:
                    self._log("  Not: " + n)
            except Exception as e:
                yazma.append(("Fatura dökümü yazılamadı", str(e).splitlines()[0]))
                self._log(f"HATA: fatura dökümü yazılamadı: {str(e).splitlines()[0]}")
                self._olay("ayrinti", traceback.format_exc())
        if sonuclar:
            try:
                yol, sonuc = self._ozet_yaz(f, ay, yil, secim, birlesik, kayit_tum)
                bitis["ozet"] = str(yol)
                tasinan = sum(1 for s in birlesik if not s.get("bu_calistirma", True))
                self._log(f"Kontrol özeti (birikimli; KDV kontrolüne bununla başlayın): {yol.name}" + (" — zaten vardı" if sonuc == "ayni" else "")
                          + (f" — {tasinan} sorgunun sonucu önceki çalıştırmalardan alındı" if tasinan else ""))
            except Exception as e:
                yazma.append(("Kontrol özeti yazılamadı", str(e).splitlines()[0]))
                self._log(f"HATA: kontrol özeti yazılamadı: {str(e).splitlines()[0]}")
                self._olay("ayrinti", traceback.format_exc())
        hatali = [s["sorgu"] for s in sonuclar if s["durum"] == "hata"]
        sayim = {k: sum(s["durum"] == k for s in sonuclar) for k in ("veri", "kayit_yok", "hata", "calistirilmadi")}
        self._log(f"Özet: {sayim['veri']} veri var, {sayim['kayit_yok']} kayıt yok, {sayim['hata']} hata, {sayim['calistirilmadi']} çalıştırılmadı.")
        if hatali:
            self._log("HATA olan sorgular: " + ", ".join(hatali))
        self._log(f"Sonuç dosyası: {dosyalar[0] if dosyalar else '-'}")
        self._log("Bitti." if not durdu else f"Durduruldu: {durdu}.")
        try:
            firma_k = cikti.firma_klasoru(f["klasor"])
            donem = firma_k / "KDV" / f"{yil}-{ay:02d}"
            bitis["donem_klasoru"] = str(donem) if donem.is_dir() else None
            bitis["tebligat_klasoru"] = str(firma_k / "E-Tebligat") if (firma_k / "E-Tebligat").is_dir() else None
        except Exception:
            pass
        bitis.update({"sayim": sayim, "fatura_sayisi": len(kayitlar), "firma": f["ad"], "donem": f"{cikti.AYLAR[ay - 1]} {yil}",
                      "acil": [f"{s['sorgu']}: {acil_metni(s)}" for s in sonuclar if acil_metni(s)],
                      "hatalar": [(s["sorgu"], s["mesaj"][:220]) for s in sonuclar if s["durum"] == "hata"] + yazma,
                      "eksik": sayim["hata"] + sayim["calistirilmadi"] + len(yazma)})
        self.son = d
        self._olay("bitti", bitis)

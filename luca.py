"""Luca işlemleri (salt okunur): firma/dönem seçimi; Defteri Kebir, Mizan, Bordro Dökümü (Excel Liste) ve Akıllı Entegrasyon listesi.

Firma seçimi Luca ekranındaki firma/dönem kutusuyla yapılır (kullanıcının yaptığı gibi).
Rapor, Luca'nın yeni rapor sistemi üzerinden doğrudan HTTP ile istenir, takip edilir ve indirilir.
"""
import io
import json
import re
import time
import zipfile
from datetime import date, datetime
from urllib.parse import urlparse

LUCA_GIRIS = "https://agiris.luca.com.tr/LUCASSO/giris.erp"   # kullanıcının giriş adresi (auygs.../giris.do doğrudan açılınca hata verir)
HOST_DESENI = re.compile(r"auygs\d*\.luca\.com\.tr")   # programın tarayıcısında numarasız (auygs.), normal Chrome'da numaralı (auygs13.) geliyor
KEBIR_BASLIK_ILK = "KEB"          # ilk sütun "KEBİR HESAP"
KEBIR_SUTUN_SAYISI = 14           # scripts/luca_kebir_ozet.py bu kadar sütun bekler


class LucaHatasi(Exception):
    pass


class Iptal(LucaHatasi):
    """Kullanıcı durdurdu (hata sayılmaz)."""


def luca_sayfasi(ctx):
    """Programın tarayıcısında açık Luca uygulama sayfasını bulur (yoksa None).
    Luca, ortak giriş ekranından ayrı bir pencerede açılır; o pencere de aynı tarayıcıya aittir."""
    adaylar = []
    for pg in ctx.pages:
        if pg.is_closed():
            continue
        if HOST_DESENI.fullmatch(urlparse(pg.url).hostname or ""):
            if pg.frame(name="frm4") is not None:
                return pg
            adaylar.append(pg)
    return adaylar[0] if adaylar else None


def sayfa_listesi(ctx):
    """Tanı için açık sekmelerin adresleri (sorgu dizesi olmadan)."""
    liste = []
    for pg in ctx.pages:
        if not pg.is_closed():
            u = urlparse(pg.url)
            liste.append(f"{u.hostname or u.scheme}{u.path}")
    return liste


# ================================================================ giriş (bilgileri kullanıcı programa girer; günlüğe yazılmaz)
GIRIS_HOST = "agiris.luca.com.tr"
PORTAL_YOLU = "/LUCASSO/main.erp"                 # girişten sonraki paket seçim ekranı
PAKET_SECICI = "[onclick*=\"'formTarget')\"]"      # "LUCA MALİ MÜŞAVİR PAKETİ" kutuları (logo + yazı; ikisi de aynı işi yapar)


def _giris_sayfasi(ctx):
    for pg in ctx.pages:
        if not pg.is_closed() and (urlparse(pg.url).hostname or "") == GIRIS_HOST:
            return pg
    return ctx.new_page()


def portal_sayfasi(ctx):
    """Girişten sonraki ortak ekran (paket seçimi) açıksa o sayfa, değilse None."""
    for pg in ctx.pages:
        if not pg.is_closed() and urlparse(pg.url).path.split(";")[0] == PORTAL_YOLU:
            return pg
    return None


# Luca'nın uyarı kutusu (eski SweetAlert2: .swal2-modal; yeni sürüm .swal2-popup). Görünürse metni (düğme yazıları hariç) döner.
_UYARI_JS = """() => {
    for (const s of ['.swal2-modal', '.swal2-popup', '.sweet-alert']) {
        const p = document.querySelector(s);
        if (p && (p.offsetWidth || p.offsetHeight)) {
            const parca = [...p.querySelectorAll('h2, .swal2-title, .swal2-content, .swal2-html-container, p')]
                .filter(e => e.offsetWidth || e.offsetHeight).map(e => e.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean);
            const t = (parca.length ? [...new Set(parca)].join(' — ') : p.innerText.replace(/\\s+/g, ' ').trim());
            if (t) return t;
        }
    }
    return '';
}"""


# Giriş sayfasının yapısı (değerler OKUNMAZ): görünür metin kutuları, resimler, çerçeveler, robot doğrulaması var mı.
_YAPI_JS = """() => {
    const gorunur = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const girdiler = [...document.querySelectorAll('input')]
        .filter(i => !['hidden', 'button', 'submit', 'checkbox', 'radio', 'image', 'reset'].includes(i.type) && gorunur(i))
        .map(i => ({name: i.name || '', id: i.id || '', type: i.type || '', dolu: !!i.value}));
    const resimler = [...document.images].filter(gorunur).map(i => ({src: (i.getAttribute('src') || '').split('?')[0].slice(-60), id: i.id || '',
                                                                     w: i.naturalWidth, h: i.naturalHeight}));
    const cerceveler = [...document.querySelectorAll('iframe')].map(f => (f.getAttribute('src') || '').slice(0, 70));
    const robot = !!document.querySelector('.g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey], iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"]');
    return {yeni: !window.__kdv_isaret, girdiler, resimler, cerceveler, robot,
            metin: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 400)};
}"""
BILINEN_ALANLAR = ("musteriNo", "kullaniciAdi", "parola")


# Sayfadaki görünür düğmeler (değer içermez: yalnız düğme yazısı ve çağırdığı işlev). Her adaya geçici bir numara özniteliği konur.
_DUGMELER_JS = """() => {
    const gorunur = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    return [...document.querySelectorAll('input[type=button], input[type=submit], input[type=image], button, a[onclick], [onclick]')]
        .filter(gorunur).map((e, i) => {
            e.setAttribute('data-kdv-no', String(i));
            return {i: i, etiket: e.tagName, tur: (e.getAttribute('type') || '').toLowerCase(),
                    yazi: (e.value || e.innerText || e.getAttribute('alt') || e.getAttribute('title') || '').replace(/\\s+/g, ' ').trim().slice(0, 40),
                    onclick: (e.getAttribute('onclick') || '').replace(/\\s+/g, ' ').slice(0, 80), sinif: String(e.className || '').slice(0, 40),
                    formda: !!e.closest('form')};
        });
}"""
GONDER_YAZILARI = {"giris", "giris yap", "devam", "devam et", "dogrula", "onayla", "tamam", "gonder"}


def _duz(metin):
    return str(metin or "").replace("İ", "i").replace("I", "ı").lower().translate(str.maketrans("çğıöşü", "cgiosu")).strip()


def _dugme_puani(a):
    """Giriş formunu gönderen düğme olma olasılığı (sanal klavye tuşları ve bilgi bağlantıları dışlanır)."""
    o, y = _duz(a["onclick"]), _duz(a["yazi"])
    if o.startswith("login_") or "keypad" in a["sinif"].lower() or o.startswith(("opentarget", "swtalert")):
        return -1
    puan = 0
    if re.search(r"giris\w*\s*\(", o) or "submit(" in o:      # girisbtn() gibi bir işlev ya da formu gönderme ("giris.erp" bağlantısı sayılmaz)
        puan += 3
    if "location" in o:                             # başka sayfaya giden bağlantı (ör. "Luca Giriş Ekranı")
        puan -= 3
    if y in GONDER_YAZILARI:
        puan += 2
    if a["etiket"] == "INPUT" and a["tur"] in ("submit", "button") and a["formda"]:
        puan += 1
    if a["tur"] == "submit" or (a["etiket"] == "BUTTON" and a["formda"]):
        puan += 1
    return puan


def _gonder_dugmesi(pg):
    """(seçici | None, adayların değer içermeyen özeti)."""
    adaylar = pg.evaluate(_DUGMELER_JS)
    ozet = "; ".join(f"{a['etiket']}/{a['tur']} yazı={a['yazi']!r} işlev={a['onclick'][:45]!r} puan={_dugme_puani(a)}"
                     for a in adaylar if _dugme_puani(a) >= 0)[:900]
    en = max(adaylar, key=_dugme_puani, default=None)
    if en is None or _dugme_puani(en) < 2:
        return None, ozet
    return f"[data-kdv-no='{en['i']}']", ozet


def _ek_girdiler(yapi):
    """Üye no / kullanıcı adı / parola dışındaki görünür metin kutuları (güvenlik kodu ya da doğrulama kodu alanı)."""
    return [g for g in yapi["girdiler"] if g["id"] not in BILINEN_ALANLAR and g["name"] not in BILINEN_ALANLAR
            and g["type"] in ("text", "tel", "number", "")]


def _girdi_secici(g):
    return f"#{g['id']}" if g["id"] else f"input[name='{g['name']}']"


def _yapi_ozeti(yapi):
    """Tanı için sayfa yapısının değer içermeyen özeti."""
    return ("girdiler=" + ", ".join(f"{g['name'] or g['id']}:{g['type']}" for g in yapi["girdiler"])
            + "; resimler=" + ", ".join(f"{r['src']}({r['w']}x{r['h']})" for r in yapi["resimler"][:8])
            + "; çerçeveler=" + ", ".join(yapi["cerceveler"][:4]) + f"; robot={yapi['robot']}")


def _dogrulama_goruntusu(pg, girdi):
    """Luca'nın istediği doğrulamanın ekran görüntüsü (PNG): giriş formu; form çok büyükse kod kutusunun çevresi."""
    try:
        form = pg.locator("form[name=girisForm]").first
        kutu = form.bounding_box()
        if kutu and 150 <= kutu["width"] <= 760 and 80 <= kutu["height"] <= 620:
            return form.screenshot()
    except Exception:
        pass
    k = pg.locator(_girdi_secici(girdi)).first.bounding_box() or {"x": 300, "y": 300, "width": 200, "height": 30}
    boyut = pg.viewport_size or {"width": 1280, "height": 900}
    x, y = max(0, k["x"] - 300), max(0, k["y"] - 220)
    return pg.screenshot(clip={"x": x, "y": y, "width": min(720, boyut["width"] - x), "height": min(340, boyut["height"] - y)})


def _gonder_ve_bekle(pg, gizli, bekle_sn, kod_girdisi=None):
    """Formu gönderir (GİRİŞ düğmesi; bulunamazsa kod kutusunda Enter) ve sonucu bekler
    -> ('tamam', '') | ('dogrulama', {resim, mesaj, yapi}) | ('robot', mesaj) | ('hata', mesaj) | ('belirsiz', mesaj)."""
    uyarilar = []
    dinle = lambda d: (uyarilar.append(d.message), d.dismiss())      # noqa: E731  (alert pencereleri)
    pg.on("dialog", dinle)
    try:
        pg.evaluate("() => { window.__kdv_isaret = 1; }")           # sayfanın yeniden yüklendiğini anlamak için
        dugme, dugmeler = _gonder_dugmesi(pg)
        if dugme:
            _tikla(pg, dugme, "Luca giriş düğmesi")
        elif kod_girdisi:                           # düğme tanınamadı: kod kutusunda Enter (kullanıcının yapacağı gibi)
            pg.locator(kod_girdisi).first.press("Enter")
        else:
            raise LucaHatasi("Luca giriş düğmesi sayfada bulunamadı. Görülen düğmeler: " + (dugmeler or "yok"))
        son = time.time() + bekle_sn
        yapi = None
        while time.time() < son:
            pg.wait_for_timeout(400)
            try:
                if urlparse(pg.url).path.split(";")[0] == PORTAL_YOLU:
                    return "tamam", ""
                metin = pg.evaluate(_UYARI_JS)
                yapi = pg.evaluate(_YAPI_JS)
            except Exception:
                continue                             # sayfa geçiş hâlinde
            if metin:
                return "hata", _ayikla(metin, gizli)[:300]
            if uyarilar:
                return "hata", _ayikla(uyarilar[-1], gizli)[:300]
            if not yapi["yeni"]:
                continue                             # form henüz gönderilmedi / yanıt gelmedi
            if yapi["robot"]:
                return "robot", "Luca bu girişte tarayıcıda robot doğrulaması istiyor; 'Tarayıcıda elle giriş yap' ile girin."
            ek = _ek_girdiler(yapi)
            if ek:                                   # Luca ek doğrulama istiyor (güvenlik kodu / doğrulama kodu)
                pg.wait_for_timeout(700)             # resim yüklensin
                mesaj = _ayikla(yapi["metin"], gizli)
                yerler = [x for x in (mesaj.find("İki aşamalı"), mesaj.find("Captcha"), mesaj.find("Güvenlik"), mesaj.find("doğrulama")) if x >= 0]
                i = min(yerler) if yerler else 0
                try:
                    dugme_ozeti = _gonder_dugmesi(pg)[1]
                except Exception:
                    dugme_ozeti = "okunamadı"
                return "dogrulama", {"resim": _dogrulama_goruntusu(pg, ek[0]), "mesaj": mesaj[i:i + 220],
                                     "yapi": f"{_yapi_ozeti(yapi)}; kod kutusu={_girdi_secici(ek[0])}; düğmeler: {dugme_ozeti}"}
        tani = ""
        try:
            yapi = yapi or pg.evaluate(_YAPI_JS)
            tani = (f"kalınan sayfa: {urlparse(pg.url).path.split(';')[0]} — {pg.title()[:60]}; ekranda: {_ayikla(yapi['metin'], gizli)[:200]!r}; "
                    f"yapı: {_yapi_ozeti(yapi)}; düğmeler: {dugmeler}")
        except Exception:
            tani = "sayfa okunamadı"
        return "belirsiz", f"Luca girişinin sonucu anlaşılamadı ({tani}). 'Tarayıcıda elle giriş' ile deneyin."
    finally:
        try:
            pg.remove_listener("dialog", dinle)
        except Exception:
            pass


def _bilgileri_yaz(pg, musteri_no, kullanici, parola, yalniz_bos=False):
    for secici, deger in (("#musteriNo", musteri_no), ("#kullaniciAdi", kullanici), ("#parola", parola)):
        kutu = pg.locator(secici).first
        if not kutu.count() or not kutu.is_visible():
            continue
        if yalniz_bos and kutu.input_value():
            continue
        kutu.fill(str(deger))


def giris_yap(ctx, musteri_no, kullanici, parola, bekle_sn=45):
    """Luca ortak giriş formunu doldurup gönderir.
    Döner: ('tamam', '') | ('dogrulama', {resim: PNG, mesaj}) | ('robot', mesaj) | ('hata', mesaj) | ('belirsiz', mesaj).
    'dogrulama': Luca ek kod istiyor (iki aşamalı doğrulama kapalıysa güvenlik kodu). Program kodu ÇÖZMEZ; resim kullanıcıya
    gösterilir, yazdığı kod giris_kod() ile gönderilir. Parola ve diğer bilgiler hiçbir yere yazılmaz; hata metninden de ayıklanır."""
    pg = _giris_sayfasi(ctx)
    pg.goto(LUCA_GIRIS, timeout=60_000)
    if urlparse(pg.url).path.split(";")[0] == PORTAL_YOLU:      # oturum zaten açık
        return "tamam", ""
    try:
        pg.wait_for_selector("#musteriNo", state="visible", timeout=30_000)
    except Exception:
        return "belirsiz", "Luca giriş sayfası beklenen yapıda açılmadı."
    _bilgileri_yaz(pg, musteri_no, kullanici, parola)
    return _gonder_ve_bekle(pg, (musteri_no, kullanici, parola), bekle_sn)


def giris_kod(ctx, musteri_no, kullanici, parola, kod, bekle_sn=45):
    """Luca'nın istediği kodu (kullanıcının yazdığı) forma yazıp gönderir; dönüş giris_yap ile aynıdır."""
    pg = _giris_sayfasi(ctx)
    if urlparse(pg.url).path.split(";")[0] == PORTAL_YOLU:
        return "tamam", ""
    try:
        yapi = pg.evaluate(_YAPI_JS)
    except Exception:
        yapi = None
    ek = _ek_girdiler(yapi) if yapi else []
    if not ek:                                       # form başa dönmüş: baştan başlanır (yeni kod istenir)
        return giris_yap(ctx, musteri_no, kullanici, parola, bekle_sn)
    _bilgileri_yaz(pg, musteri_no, kullanici, parola, yalniz_bos=True)
    pg.locator(_girdi_secici(ek[0])).first.fill(str(kod))
    return _gonder_ve_bekle(pg, (musteri_no, kullanici, parola, kod), bekle_sn, kod_girdisi=_girdi_secici(ek[0]))


def _tikla(pg, secici, ad):
    """Öğeye tıklar. Aynı işi yapan birden çok öğe varsa (logo + yazı) sırayla dener; öğe başka bir öğenin altında kalıyor ya da
    sürekli hareket ediyorsa (Playwright tıklamayı güvenli bulmaz) öğenin KENDİ tıklama olayını tetikler (kullanıcının tıklamasıyla aynı işlev)."""
    kutular = pg.locator(secici)
    n = kutular.count()
    if not n:
        raise LucaHatasi(f"{ad} sayfada bulunamadı.")
    for i in range(min(n, 4)):
        try:
            kutular.nth(i).click(timeout=4000)
            return "tıklandı"
        except Exception:
            continue
    try:
        kutular.first.dispatch_event("click")
        return "olay tetiklendi"
    except Exception as e:
        raise LucaHatasi(f"{ad} tıklanamadı: {str(e).splitlines()[0][:150]}")


def _ayikla(metin, gizliler):
    for g in gizliler:
        if g and len(str(g)) >= 3:
            metin = metin.replace(str(g), "•••")
    return metin


def _pencere_ozeti(ctx):
    """Tanı: açık pencerelerin adresi, başlığı, çerçeveleri ve ekrandaki ilk metin (giriş bilgisi içermez)."""
    ozet = []
    for pg in ctx.pages:
        if pg.is_closed():
            continue
        u = urlparse(pg.url)
        try:
            b = pg.evaluate("""() => ({baslik: document.title || '', metin: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 140),
                                       cerceve: [...document.querySelectorAll('frame, iframe')].map(f => f.name || f.id || '?').slice(0, 12)})""")
        except Exception:
            b = {"baslik": "?", "metin": "okunamadı", "cerceve": []}
        ozet.append(f"{u.hostname or u.scheme}{u.path.split(';')[0]} [başlık {b['baslik'][:40]!r}; çerçeveler {','.join(b['cerceve']) or 'yok'}; ekranda {b['metin']!r}]")
    return " || ".join(ozet) or "açık pencere yok"


def _uygulama_hazir(ctx):
    pg = luca_sayfasi(ctx)
    if pg is None:
        return None
    fr = pg.frame(name="frm4")
    try:
        return pg if fr is not None and fr.query_selector("select[name=SirketCombo]") else None
    except Exception:
        return None


def uygulama_ac(ctx, deneme_sayisi=3, bekle_sn=25, gunluk=lambda m: None):
    """Ortak ekranda 'LUCA MALİ MÜŞAVİR PAKETİ'ni açar (yeni pencere) ve firma kutulu uygulama sayfasını döner.
    Luca penceresi bazen boş açılır (kullanıcı da elle kullanırken görüyor); o zaman pencere kapatılıp kutuya yeniden tıklanır."""
    pg = _uygulama_hazir(ctx)
    if pg is not None:
        return pg
    portal = portal_sayfasi(ctx)
    if portal is None:
        raise LucaHatasi("Luca'ya giriş yapılmamış (ortak giriş ekranı açık değil).")
    uyari = portal.evaluate(_UYARI_JS)
    if uyari:                                       # ortak ekranda Luca'nın bir uyarısı açık: kullanıcı görmeli, program kapatmaz
        raise LucaHatasi(f"Luca ortak ekranında bir uyarı var: {uyari[:220]!r}. 'Tarayıcıda elle giriş yap' ile açıp uyarıyı kapatın.")
    try:                                            # ortak ekran tam yüklenmeden tıklanmasın (kullanıcı da yüklenince tıklar)
        portal.wait_for_load_state("load", timeout=15_000)
    except Exception:
        pass
    portal.wait_for_timeout(1500)
    durum = ""
    yanitlar = []                                   # tanı: Luca sunucusunun sayfa yanıtları (durum kodu, yönlendirme, boyut; içerik yazılmaz)

    def yanit(r):
        try:
            if r.request.resource_type != "document" or not (urlparse(r.url).hostname or "").endswith("luca.com.tr"):
                return
            b = r.headers
            konum = f" -> {urlparse(b['location']).path.split(';')[0]}" if b.get("location") else ""
            yanitlar.append(f"{r.request.method} {urlparse(r.url).path.split(';')[0]}: {r.status}{konum} [{b.get('content-type', '?')[:24]}, {b.get('content-length', '?')} bayt]")
        except Exception:
            pass

    def basarisiz(r):
        try:
            if r.resource_type == "document":
                yanitlar.append(f"BAŞARISIZ {r.method} {urlparse(r.url).path.split(';')[0]}: {r.failure}")
        except Exception:
            pass
    ctx.on("response", yanit)
    ctx.on("requestfailed", basarisiz)
    try:
        for deneme in range(1, deneme_sayisi + 1):
            onceki = [p for p in ctx.pages if not p.is_closed()]
            del yanitlar[:]
            nasil = _tikla(portal, PAKET_SECICI, "'LUCA MALİ MÜŞAVİR PAKETİ' kutusu")
            son = time.time() + bekle_sn
            while time.time() < son:
                portal.wait_for_timeout(500)         # gerçek çağrı: yeni pencere olayları işlenir
                pg = _uygulama_hazir(ctx)
                if pg is not None:
                    return pg
            durum = _pencere_ozeti(ctx)
            ek = []
            for yeni_pg in [p for p in ctx.pages if not p.is_closed() and p not in onceki]:
                try:
                    ek.append(str(yeni_pg.evaluate("""() => ({hazir: document.readyState, html: document.documentElement ? document.documentElement.outerHTML.length : 0,
                        betik: document.scripts.length, dis: window.outerWidth + 'x' + window.outerHeight, ic: window.innerWidth + 'x' + window.innerHeight,
                        acan: !!window.opener, ad: window.name})""")))
                except Exception as e:
                    ek.append("okunamadı: " + str(e).splitlines()[0][:80])
            gunluk(f"Luca paketi {deneme}. denemede {bekle_sn} sn içinde açılmadı ({nasil}). Pencereler: {durum}. "
                   f"Sunucu yanıtları: {'; '.join(yanitlar) or 'yok'}. Yeni pencere: {' | '.join(ek) or 'yok'}")
            for yeni_pg in [p for p in ctx.pages if not p.is_closed() and p not in onceki]:      # bu denemede açılan boş/yarım pencere
                try:
                    yeni_pg.close()
                except Exception:
                    pass
            if portal.is_closed() or urlparse(portal.url).path.split(";")[0] != PORTAL_YOLU:
                break                                # ortak ekran gitti: yeniden denemenin anlamı yok
    finally:
        for olay, isleyici in (("response", yanit), ("requestfailed", basarisiz)):
            try:
                ctx.remove_listener(olay, isleyici)
            except Exception:
                pass
    raise LucaHatasi(f"Luca uygulaması {deneme_sayisi} denemede açılmadı (ayrıntı günlükte). Son görülen: {durum[:300]}")


def firma_listesi(pg):
    """Luca firma kutusundaki firmalar: [(sirket_id, ad)]."""
    fr = _cerceve(pg, "frm4")
    return [(str(v), str(t)) for v, t in fr.evaluate(
        "() => Array.from(document.querySelector('select[name=SirketCombo]').options).map(o => [o.value, o.text.trim()])")
            if v and str(v) != "0" and str(t).strip()]


def _cerceve(pg, ad, bekle_sn=15):
    son = time.time() + bekle_sn
    while time.time() < son:
        fr = pg.frame(name=ad)
        if fr is not None:
            try:
                if fr.query_selector("select[name=SirketCombo]") or ad != "frm4":
                    return fr
            except Exception:
                pass
        time.sleep(0.5)
    raise LucaHatasi(f"Luca ekranında '{ad}' çerçevesi bulunamadı (Luca ana sayfası açık mı?).")


def secili_firma(pg):
    fr = _cerceve(pg, "frm4")
    return fr.evaluate("""() => {
        const s = document.querySelector('select[name=SirketCombo]'), d = document.querySelector('select[name=DonemCombo]');
        return {sirket_id: s.value, sirket: s.options[s.selectedIndex] ? s.options[s.selectedIndex].text.trim() : '',
                donem_id: d.value, donem: d.options[d.selectedIndex] ? d.options[d.selectedIndex].text.trim() : ''};
    }""")


def _taban(pg):
    return f"https://{urlparse(pg.url).hostname}/Luca/"


def _baslik(pg, sayfa="luca.do", json_mu=False):
    b = {"X-Requested-With": "XMLHttpRequest", "Referer": _taban(pg) + sayfa,
         "Origin": f"https://{urlparse(pg.url).hostname}"}
    if json_mu:
        b["Content-Type"] = "application/json"
    return b


def _json_post(ctx, pg, yol, veri, sayfa="raporKebir.do"):
    r = ctx.request.post(_taban(pg) + yol, data=json.dumps(veri, ensure_ascii=False),
                         headers=_baslik(pg, sayfa, json_mu=True), timeout=120_000)
    if r.status != 200:
        raise LucaHatasi(f"{yol}: HTTP {r.status}")
    try:
        return r.json()
    except Exception:
        raise LucaHatasi(f"{yol}: JSON yerine başka yanıt geldi (Luca oturumu düşmüş olabilir).")


def donem_bilgisi(ctx, pg, sirket_id, donem_id):
    d = _json_post(ctx, pg, "genel/donem_getir.jq", {"donem_id": str(donem_id), "sirket_id": str(sirket_id)}, "luca.do")
    if not isinstance(d, dict) or str(d.get("sirket_id")) != str(sirket_id) or str(d.get("donem_id")) != str(donem_id):
        raise LucaHatasi("Luca dönem bilgisi beklenen firma/dönemle eşleşmedi.")
    return d


def oturum_var_mi(ctx):
    """Luca sayfası açık ve kimlik isteyen bir JSON adresi cevap veriyorsa True."""
    pg = luca_sayfasi(ctx)
    if pg is None:
        return False, "Luca uygulama penceresi bulunamadı"
    try:
        s = secili_firma(pg)
    except Exception as e:
        return False, f"Luca penceresi bulundu ({urlparse(pg.url).hostname}{urlparse(pg.url).path}) ama firma kutusu okunamadı: {str(e).splitlines()[0][:150]}"
    try:
        donem_bilgisi(ctx, pg, s["sirket_id"], s["donem_id"])
    except Exception as e:
        return False, f"Luca penceresi ve firma ({s['sirket']}) bulundu ama Luca sorgusu cevap vermedi: {str(e).splitlines()[0][:150]}"
    return True, f"{urlparse(pg.url).hostname} — açık firma: {s['sirket']}"


def _tarih(t):
    return datetime.strptime(t.strip(), "%d/%m/%Y").date()


def firma_sec(pg, sirket_id, yil, ay, gunluk, iptal=lambda: False):
    """Luca'da firmayı ve KDV ayını içeren mali dönemi seçer; seçili dönem kimliğini döner."""
    hedef = date(yil, ay, 1)
    s = secili_firma(pg)
    if s["sirket_id"] == str(sirket_id):
        try:
            b, e = [_tarih(x) for x in s["donem"].split(" - ")]
            if b <= hedef <= e:
                gunluk(f"Luca: firma zaten açık ({s['sirket']}, {s['donem']}).")
                return s["donem_id"]
        except ValueError:
            pass
    fr = _cerceve(pg, "frm4")
    if not fr.query_selector(f"select[name=SirketCombo] option[value='{sirket_id}']"):
        raise LucaHatasi(f"Luca firma listesinde {sirket_id} numaralı firma yok.")
    fr.select_option("select[name=SirketCombo]", str(sirket_id))      # change olayı: dönem listesi yenilenir
    donem_id, donem_metni = None, None
    son = time.time() + 15
    while time.time() < son and donem_id is None:
        secenekler = fr.evaluate("() => Array.from(document.querySelector('select[name=DonemCombo]').options).map(o => [o.value, o.text.trim()])")
        for deger, metin in secenekler:
            try:
                b, e = [_tarih(x) for x in metin.split(" - ")]
            except ValueError:
                continue
            if b <= hedef <= e:
                donem_id, donem_metni = deger, metin
                break
        if donem_id is None:
            time.sleep(0.5)
    if donem_id is None:
        raise LucaHatasi(f"Luca'da {ay:02d}/{yil} ayını içeren mali dönem bulunamadı.")
    fr.select_option("select[name=DonemCombo]", donem_id)
    if iptal():
        raise Iptal("İptal edildi.")
    fr.click("button")
    son = time.time() + 45
    while time.time() < son:
        time.sleep(1)
        try:
            s = secili_firma(pg)
        except Exception:
            continue
        if s["sirket_id"] == str(sirket_id) and s["donem_id"] == str(donem_id):
            gunluk(f"Luca: firma açıldı ({s['sirket']}, {s['donem']}).")
            return donem_id
    raise LucaHatasi("Luca'da firma değişimi doğrulanamadı (45 sn).")


def _kebir_formu():
    return {"hesap_bas": "", "hesap_bit": "", "tarih_bas": "", "tarih_bit": "", "fis_birlestir": "0",
            "fis_tipi": None, "fis_kodu": None, "sayfa_atla": "0", "bakiye_goster": "1", "aciklama_tur": "1",
            "hesap_goster": "1", "fis_no_goster": "1", "madde_no_goster": "1", "evrak_no_goster": "1",
            "evrak_tarihi_goster": "1", "fis_tarihi_goster": "1", "siralama_tipi": "2", "ay_toplam_goster": "0",
            "kebir_toplam_goster": "1", "genel_toplam_goster": "0", "alanlar": "1", "doviz": ["tl"],
            "sistem_doviz_sembol": "TL", "tarih_yazdir": "H", "sayfa_numarasi_yazdir": "H", "report_type": "LIST",
            "font": "Calibri", "alt_bilgi_yazdir": "H"}


def xlsx_satirlari(bayt):
    """Luca'nın 'Excel Liste' dosyasının ilk sayfasındaki satırlar (dosyadaki boyut bilgisi yanlış olduğundan sıfırlanır)."""
    import warnings
    from openpyxl import load_workbook
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(io.BytesIO(bayt), read_only=True)
    try:
        ws = wb.worksheets[0]
        ws.reset_dimensions()
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def kebir_xlsx_kontrol(bayt):
    """Geçerli xlsx mi ve beklenen başlık yapısında mı? Uyarı listesi döner (boş = sorun yok)."""
    uyarilar = []
    try:
        with zipfile.ZipFile(io.BytesIO(bayt)) as z:
            if "xl/workbook.xml" not in z.namelist():
                return ["İndirilen dosya Excel (xlsx) değil."]
        satirlar = xlsx_satirlari(bayt)
        ilk = satirlar[0] if satirlar else []
        dolu = [v for v in ilk if v not in (None, "")]
        if not dolu or not str(dolu[0]).upper().startswith(KEBIR_BASLIK_ILK):
            uyarilar.append(f"İlk satır beklenen kebir başlığı değil: {str(dolu[:3])[:80]}")
        if len(ilk) != KEBIR_SUTUN_SAYISI:
            uyarilar.append(f"Sütun sayısı {len(ilk)} (beklenen {KEBIR_SUTUN_SAYISI}); kebir özet betiği okuyamayabilir.")
    except Exception as e:
        uyarilar.append(f"Excel dosyası açılamadı: {e}")
    return uyarilar


# ================================================================ yeni rapor sistemi (jasper.jq): ortak akış
def _musteri_id(pg):
    """Menü bağlantılarındaki müşteri (büro) numarası; rapor sayfaları bu parametreyle açılır."""
    fr = _cerceve(pg, "frm2")
    m = re.search(r"c_musteri_id=(\d+)", fr.content())
    if not m:
        raise LucaHatasi("Luca menüsünde müşteri numarası bulunamadı.")
    return m.group(1)


def _acik_firma_dogrula(pg, sirket_id, donem_id):
    s = secili_firma(pg)
    if s["sirket_id"] != str(sirket_id) or s["donem_id"] != str(donem_id):
        raise LucaHatasi("Luca'da açık firma/dönem değişmiş; işlem durduruldu (tarayıcıda firmayı değiştirmeyin).")
    return s


def rapor_formu(pg, sayfa, degisiklik=None, js_duzenle=None):
    """Rapor ekranını Luca'nın içerik çerçevesinde açar ve varsayılan parametreleri Luca'nın kendi form2json'u ile okur.
    degisiklik: {alan adı: değer} ekranda ayarlanır. js_duzenle: sayfadaki düzenleme işlevinin adı (örn. bordroda
    'Luca.rapor_form_duzenle'). Böylece Luca ekrana yeni alan eklese de istek ekranla aynı kalır."""
    fr = _cerceve(pg, "frm3")
    fr.goto(_taban(pg) + sayfa + ("&" if "?" in sayfa else "?") + "c_musteri_id=" + _musteri_id(pg), timeout=60_000)
    fr = _cerceve(pg, "frm3")
    try:
        fr.wait_for_selector(".yeni-rapor-paremetreleri", state="attached", timeout=20_000)
    except Exception:
        raise LucaHatasi(f"Luca rapor ekranı ({sayfa}) beklenen yapıda açılmadı.")
    form = fr.evaluate(
        """([d, duzenle]) => {
            if (typeof form2json !== 'function') return null;
            for (const k in d) {
                const e = document.querySelector('.yeni-rapor-paremetreleri [name="' + k + '"]');
                if (!e) return {__eksik: k};
                e.value = d[k];
                if (e.value !== String(d[k])) return {__eksik: k + '=' + d[k]};
            }
            let f = form2json('.yeni-rapor-paremetreleri');
            if (duzenle) {
                const fn = duzenle.split('.').reduce((o, a) => o && o[a], window);
                if (typeof fn !== 'function') return {__eksik: duzenle};
                f = fn(f) || f;
            }
            return f;
        }""", [degisiklik or {}, js_duzenle])
    if not isinstance(form, dict):
        raise LucaHatasi(f"Luca rapor ekranında ({sayfa}) parametreler okunamadı.")
    if "__eksik" in form:
        raise LucaHatasi(f"Luca rapor ekranında ({sayfa}) beklenen alan/değer yok: {form['__eksik']}")
    return form


def _listeye_cevir(form, alanlar):
    """Ekranın yaptığı dönüşüm: 'Tümü'/boş -> None, virgüllü metin -> liste; döviz boşsa ['tl']."""
    for k in alanlar:
        v = form.get(k)
        form[k] = None if v in (None, "", "Tümü") else (v.split(",") if isinstance(v, str) else v)
    d = form.get("doviz")
    form["doviz"] = ["tl"] if not d or (isinstance(d, str) and not d.strip()) else (d.split(",") if isinstance(d, str) else d)
    return form


def rapor_al(ctx, pg, sirket_id, donem_id, rapor_tur, sayfa, form, gunluk, iptal=lambda: False, zaman_asimi_sn=300, ad="rapor"):
    """Luca yeni rapor sistemi: isteği gönderir, hazır olmasını bekler, dosyayı indirir. (bayt, dönem bilgisi) döner.
    E-posta ile gönderme seçenekleri HİÇBİR ZAMAN kullanılmaz (email_gonder boş bırakılır)."""
    _acik_firma_dogrula(pg, sirket_id, donem_id)
    takip_govde = {"donem_id": str(donem_id), "params": {"raporTur": rapor_tur}}
    donem = donem_bilgisi(ctx, pg, sirket_id, donem_id)
    onceki = {}
    try:
        onceki = _json_post(ctx, pg, "genel/rapor_takip.jq", takip_govde, sayfa) or {}
    except LucaHatasi:
        pass
    form = dict(form)
    form.update({"donem_id": str(donem_id), "sirket_id": str(sirket_id), "tur": rapor_tur, "email_hesap": None, "email_gonder": ""})
    form.pop("email_to", None)
    form.pop("mesaj", None)
    donem_metin = dict(donem)
    donem_metin["donem"] = str(donem_metin.get("donem", ""))
    istek = {"donem_id": str(donem_id),
             "donem": json.dumps(donem_metin, ensure_ascii=False, separators=(",", ":")),
             "form": json.dumps(form, ensure_ascii=False, separators=(",", ":"))}
    r = ctx.request.post(_taban(pg) + "jasper.jq?rapor_tur=" + rapor_tur, data=json.dumps(istek, ensure_ascii=False),
                         headers=_baslik(pg, sayfa, json_mu=True), timeout=120_000)
    if r.status != 200:
        raise LucaHatasi(f"{ad}: rapor isteği HTTP {r.status}")
    try:
        cevap = r.json()
    except Exception:
        raise LucaHatasi(f"{ad}: rapor isteğine JSON yerine başka yanıt geldi (Luca oturumu düşmüş olabilir).")
    if not isinstance(cevap, dict) or not cevap.get("success"):
        raise LucaHatasi(f"{ad}: Luca rapor isteğini kabul etmedi: " + str((cevap or {}).get("error") or cevap)[:200])
    gunluk(f"Luca: {ad} istendi, hazırlanıyor…")
    son = time.time() + zaman_asimi_sn
    son_aciklama = ""
    while True:
        if iptal():
            try:
                _json_post(ctx, pg, "genel/rapor_sonlandir.jq", takip_govde, sayfa)
            except Exception:
                pass
            raise Iptal("İptal edildi.")
        if time.time() > son:
            raise LucaHatasi(f"{ad} {zaman_asimi_sn} sn içinde hazırlanmadı (son durum: {son_aciklama}).")
        time.sleep(1.5)
        t = _json_post(ctx, pg, "genel/rapor_takip.jq", takip_govde, sayfa) or {}
        aciklama = t.get("durumAciklama") or ""
        if aciklama != son_aciklama:
            gunluk("Luca: " + aciklama)
            son_aciklama = aciklama
        durum = t.get("durum")
        yeni = t.get("baslangicTarihi") and t.get("baslangicTarihi") != onceki.get("baslangicTarihi")
        if durum == 150 and yeni:
            break
        if yeni and durum not in (100, 150, None):   # yalnız bizim isteğimizin durumu değerlendirilir
            raise LucaHatasi(f"{ad}: Luca rapor hatası (durum {durum}): {aciklama}")
    r = ctx.request.post(_taban(pg) + "genel/rapor_indir.jq",
                         form={"donem_id": str(donem_id), "filename": f"{rapor_tur}_{donem.get('kisa_ad', '')}", "raporTur": rapor_tur},
                         headers={"Referer": _taban(pg) + sayfa}, timeout=300_000)
    bayt = r.body()
    if r.status != 200 or not bayt.startswith(b"PK"):
        raise LucaHatasi(f"{ad} indirilemedi (HTTP {r.status}, {len(bayt)} bayt).")
    return bayt, donem


# ================================================================ raporlar
def kebir_indir(ctx, pg, sirket_id, donem_id, gunluk, iptal=lambda: False, zaman_asimi_sn=300):
    """Açık firma ve mali dönemin tüm yıl Defteri Kebir'i (Excel Liste). (bayt, dönem bilgisi) döner."""
    return rapor_al(ctx, pg, sirket_id, donem_id, "defteri_kebir", "raporKebir.do", _kebir_formu(), gunluk, iptal, zaman_asimi_sn,
                    ad="Defteri Kebir")


def mizan_indir(ctx, pg, sirket_id, donem_id, bas, bit, gunluk, iptal=lambda: False, zaman_asimi_sn=300):
    """bas/bit: date. Tarih aralığı mizanı (Excel Liste; borç, alacak, borç/alacak bakiyesi). (bayt, dönem bilgisi) döner."""
    form = rapor_formu(pg, "raporMizanHazirla.do", {"tarih_ilk": f"{bas:%d/%m/%Y}", "tarih_son": f"{bit:%d/%m/%Y}",
                                                     "ay": "", "report_type": "LIST"})
    _listeye_cevir(form, ("fis_tipi", "fis_kodu", "hesap_tipi"))
    return rapor_al(ctx, pg, sirket_id, donem_id, "mizan", "raporMizanHazirla.do", form, gunluk, iptal, zaman_asimi_sn, ad="Mizan")


def bordro_indir(ctx, pg, sirket_id, donem_id, ay, yil, gunluk, iptal=lambda: False, zaman_asimi_sn=300):
    """Aylık Bordro Dökümü (Excel Liste). (bayt, dönem bilgisi) döner. Bordro yoksa Luca hata/boş rapor verebilir."""
    form = rapor_formu(pg, "raporPersonelSonucAction.do", {"ay": str(ay), "yil": str(yil), "report_type": "LIST"},
                       js_duzenle="Luca.rapor_form_duzenle")
    return rapor_al(ctx, pg, sirket_id, donem_id, "bordro", "raporPersonelSonucAction.do", form, gunluk, iptal, zaman_asimi_sn,
                    ad="Bordro Dökümü")


# ================================================================ Akıllı Entegrasyon (yalnız LİSTE okunur)
# DİKKAT: bu ekrandaki kaydet/sil/fiş kes/muhasebeleştir/iptal kaydet adresleri (gib530/fatura_kaydet.jq, fis_kes.jq, ...)
# programdan ASLA çağrılmaz; yalnız gib530/fatura_list.jq (liste) kullanılır.
AE_TURLERI = {"alis": ("ortak_fatura_alis", "1"), "satis": ("ortak_fatura_satis", "2")}


def akilli_entegrasyon_listesi(ctx, pg, sirket_id, yon, bas, bit, iptal=lambda: False):
    """yon: 'alis' | 'satis'; bas/bit: date (belge tarihi). Luca'nın Akıllı Entegrasyon deposundaki belgeler:
    [{belge_tarihi, ettn, belge_numarasi, alici_vkn_tckn, alici_unvan_ad_soyad, onay_durumu, fis_no, iptal_itiraz,
      iptal_itiraz_durumu, belge_turu, entegrasyon_tur, mukerrer_sayisi, ...}]. Luca'ya henüz getirilmemiş belgeler burada yoktur."""
    tur, gelir_gider = AE_TURLERI[yon]
    if secili_firma(pg)["sirket_id"] != str(sirket_id):
        raise LucaHatasi("Luca'da açık firma değişmiş; işlem durduruldu.")
    tum, ilk_belge, bitti = [], None, False
    for sayfa_no in range(1, 51):
        if iptal():
            raise Iptal("İptal edildi.")
        veri = {"sayfaNo": sayfa_no, "baslangic": f"{bas:%d/%m/%Y}", "bitis": f"{bit:%d/%m/%Y}", "vkn": "", "belgeNo": "", "unvan": "",
                "entegrasyonTur": "", "muhasebelesmis": "-1", "belgeTuru": "", "tur": tur, "gelirGider": gelir_gider, "sinif": 1,
                "sirket_id": str(sirket_id)}
        j = _json_post(ctx, pg, "gib530/fatura_list.jq", veri, "gib530.do?tur=" + tur)
        if not isinstance(j, list):
            raise LucaHatasi("Akıllı Entegrasyon listesi beklenen yapıda değil: " + str(j)[:200])
        if not j:
            bitti = True
            break
        anahtar = (j[0].get("ettn"), j[0].get("belge_numarasi"))
        if anahtar == ilk_belge:                   # sayfalama yoksa aynı sayfa döner: dur
            bitti = True
            break
        if ilk_belge is None:
            ilk_belge = anahtar
        tum.extend(j)
    if not bitti:                                  # kesilmiş liste eksiksiz sayılmasın
        raise LucaHatasi("Akıllı Entegrasyon listesi 50 sayfayı aştı; liste tamamlanamadı.")
    return tum


# ================================================================ UTTS akaryakıt giderleri (kullanıcı kararı 19.11.2026)
# Akıllı Entegrasyon > GİB e-Belge > UTTS Akaryakıt Giderleri ekranının KENDİ işlevleri kullanılır (javascript/gib530Akaryakit.js):
#  - "GİB'den getir" (uttsFisGetir): akaryakit_plaka_liste -> her hafta x her kayıtlı plaka akaryakit_getir -> gelen her fiş için
#    akaryakit_kaydet. Fiş yalnız Luca'nın UTTS LİSTESİNE eklenir; MUHASEBE FİŞİ KESİLMEZ. Luca aralığın aynı ay içinde olmasını ister.
#    Çift kaydı önlemek için Luca'da zaten olan ETTN'ler kaydedilmez.
#  - "Ünvan sorgula" (gib530.js unvan_sorgula): cariAdresSorgulamaAction.do?islem=seciliSorgula (GİB) -> gib530/unvan_kaydet.jq;
#    yalnız ünvanı BOŞ UTTS kayıtlarına istasyon ünvanı yazılır (tur='utts').
#  - Excel (excel_utts): gib530/akaryakit_excel.jq -> gib530/excel.jq?uid=
# ASLA çağrılmaz: akaryakit_fis_kes.jq, akaryakit_sil.jq, akaryakit_plaka_tanim.jq, cari_hesap_olustur.jq, muhasebeleştir işlevleri.
UTTS_SAYFA = "gib530.do?tur=utts"
_UTTS_ISLEVLER = ("fetchData", "handleOkcResponse", "plakaUniqueMap", "haftalikAraliklar", "session")
_UTTS_DEGISKENLER = ("muhasebeciVkn", "sinif", "menu_tur")


def utts_ekrani(pg, sirket_id, bekle_sn=30):
    """UTTS ekranını Luca'nın içerik çerçevesinde açar; ekranın işlevleri/değişkenleri yüklenince çerçeveyi döndürür."""
    fr = _cerceve(pg, "frm3")
    fr.goto(_taban(pg) + UTTS_SAYFA + "&c_musteri_id=" + _musteri_id(pg), timeout=60_000)
    son, eksik = time.time() + bekle_sn, None
    while time.time() < son:
        fr = _cerceve(pg, "frm3")
        try:
            eksik = fr.evaluate("""(adlar) => adlar.filter(a => { try { return typeof eval(a) === 'undefined'; } catch (e) { return true; } })""",
                                list(_UTTS_ISLEVLER + _UTTS_DEGISKENLER))
        except Exception:
            eksik = None                            # sayfa geçiş hâlinde
        if eksik == []:
            break
        time.sleep(0.5)
    else:
        raise LucaHatasi("Luca UTTS ekranı beklenen yapıda açılmadı" + (f" (eksik: {', '.join(eksik)})" if eksik else "") + ".")
    durum = fr.evaluate("() => ({tur: menu_tur, sirket: String(session('SIRKET_ID')), sinif: String(sinif)})")
    if durum["tur"] != "utts":
        raise LucaHatasi(f"Luca UTTS yerine başka ekran açıldı ({durum['tur']}).")
    if durum["sirket"] != str(sirket_id):
        raise LucaHatasi("Luca UTTS ekranında açık firma beklenen firma değil; işlem durduruldu.")
    return fr


def utts_plakalar(fr):
    """Luca'da UTTS için kayıtlı plakalar: [{plaka, binekMi}] (ekranın plakaUniqueMap'iyle)."""
    r = fr.evaluate("""async () => {
        const res = await fetchData("gib530/akaryakit_plaka_liste.jq?t=" + Date.now(),
                                    {sirket_id: session("SIRKET_ID"), donem_id: session("DONEM_ID"), params: {firmaSinif: sinif}});
        if (!res || res.error) return {hata: (res && res.error) || "boş yanıt"};
        return {liste: plakaUniqueMap(res.list || []).map(p => ({plaka: p.plaka, binekMi: p.binekMi}))};
    }""")
    if r.get("hata"):
        raise LucaHatasi("UTTS plaka listesi alınamadı: " + str(r["hata"])[:200])
    return r["liste"]


def utts_haftalar(fr, bas, bit):
    """Luca'nın kendi haftalık bölmesi (aralık aynı ay içinde olmalı): [{start, end}] (YYYY-AA-GG)."""
    h = fr.evaluate("([a, b]) => haftalikAraliklar(a, b)", [f"{bas:%Y-%m-%d}", f"{bit:%Y-%m-%d}"])
    if not h:
        raise LucaHatasi(f"UTTS tarih aralığı Luca'ca kabul edilmedi ({bas:%d.%m.%Y} - {bit:%d.%m.%Y}; aynı ay içinde olmalı).")
    return h


def utts_getir(fr, plaka, binek_mi, bas_iso, bit_iso, mevcut_ettn):
    """Bir plaka + bir hafta için GİB'den UTTS fişlerini getirir ve Luca'nın kendi işleviyle (handleOkcResponse) Luca'ya kaydeder.
    Luca'da zaten olan ETTN'ler kaydedilmez. Döner: {hata, basari, gelen, atlanan, kaydedilen, kayit_hatalari, ettnler}"""
    return fr.evaluate("""async ([plaka, binekMi, bas, bit, mevcut]) => {
        let mv, mt;
        try { mv = mukellefVkn; } catch (e) {}
        try { mt = mukellefTckn; } catch (e) {}
        const out = {hata: null, basari: null, gelen: 0, atlanan: 0, kaydedilen: 0, kayit_hatalari: [], ettnler: []};
        const res = await fetchData("gib530/akaryakit_getir.jq?t=" + Date.now(),
            {plaka: plaka, donemBaslangicTarihi: bas, donemBitisTarihi: bit, muhasebeciVkn: muhasebeciVkn, mukellefVknTckn: mv ? mv : mt});
        if (!res) { out.hata = "boş yanıt"; return out; }
        out.hata = res.error || null;
        out.basari = res.success || null;
        if (!res.success || !res.data || !res.data.fileList) return out;
        const NS = "http://earsiv.efatura.gov.tr", gorulen = new Set(mevcut), yeni = [];
        for (const x of res.data.fileList) {
            out.gelen++;
            let ettn = "";
            try {
                const d = new DOMParser().parseFromString(x, "application/xml");
                const f = d.getElementsByTagNameNS(NS, "pompaYazarKasaFis")[0];
                ettn = ((f && f.getElementsByTagNameNS(NS, "ETTN")[0]) || {}).textContent || "";
                ettn = ettn.trim();
            } catch (e) {}
            if (ettn && gorulen.has(ettn)) { out.atlanan++; continue; }
            if (ettn) gorulen.add(ettn);            // aynı yanıtta iki kez gelmesin; "mevcut"a yalnız kaydedilince eklenir
            yeni.push(x);
        }
        if (!yeni.length) return out;
        const asil = fetchData;
        fetchData = async (u, v) => {
            const r = await asil(u, v);
            if (String(u).indexOf("akaryakit_kaydet") >= 0) {
                const e = v && v.params ? v.params.ettn : "";
                if (!r || typeof r !== "object") out.kayit_hatalari.push((e || "?") + ": boş yanıt");
                else if (r.error) out.kayit_hatalari.push((e || "?") + ": " + r.error);
                else if (r.warning) out.kayit_hatalari.push((e || "?") + ": " + r.warning);
                else { out.kaydedilen++; if (e) out.ettnler.push(e); }   // ekran da hata yoksa kaydedilmiş sayar
            }
            return r;
        };
        try {
            await handleOkcResponse(Object.assign({}, res, {data: Object.assign({}, res.data, {fileList: yeni})}), binekMi);
        } catch (e) {
            out.kayit_hatalari.push("fiş işlenemedi: " + e);
        } finally {
            fetchData = asil;
        }
        return out;
    }""", [plaka, binek_mi, bas_iso, bit_iso, sorted(mevcut_ettn)])


def utts_liste(fr, bas, bit):
    """Luca'nın UTTS listesi (belge tarihi aralığı): [{ettn, uttsFisNo, mukellefVknTckn, mukellefUnvan, plaka, aracTipi, satisTipi,
    odemeTipi, belgeTarihi, odemeTutar, yakitAdi, miktar, birimTutar, matrah, vergiTutari, vergiOrani, toplamTutar, fisNo, fisId, ...}]"""
    r = fr.evaluate("""async ([bas, bit]) => {
        const tum = []; let ilk = null;
        for (let no = 1; no <= 50; no++) {
            const res = await fetchData("gib530/akaryakit_liste.jq?t=" + Date.now(),
                {params: {sayfaNo: no, sinif: sinif, baslangic: bas, bitis: bit}, donem_id: session("DONEM_ID")});
            if (!res || !res.success) return {hata: (res && res.error) || "Hata oluştu."};
            const l = res.list || [];
            if (!l.length) return {liste: tum};
            const a = (l[0].ettn || "") + "#" + (l[0].uttsFisNo || "");
            if (a === ilk) return {liste: tum};          // sayfalama yoksa aynı sayfa döner
            if (ilk === null) ilk = a;
            tum.push(...l);
        }
        return {hata: "UTTS listesi 50 sayfayı aştı; liste tamamlanamadı."};
    }""", [f"{bas:%d/%m/%Y}", f"{bit:%d/%m/%Y}"])
    if r.get("hata"):
        raise LucaHatasi("UTTS listesi alınamadı: " + str(r["hata"])[:200])
    return r["liste"]


def utts_unvan_sorgula(fr, sirket_id, kayitlar):
    """Ekranın 'Ünvan sorgula' işlemi, yalnız ünvanı BOŞ kayıtlar için: GİB'den ünvan (cariAdresSorgulamaAction) + unvan_kaydet.
    Döner: {yazilan, bulunamayan: [vkn], hatalar: [metin]}"""
    istek = [{"belgeNumarasi": str(k.get("uttsFisNo") or ""), "ettn": str(k.get("ettn") or ""), "aliciVknTckn": str(k.get("mukellefVknTckn") or "").strip(),
              "belgeTarihi": str(k.get("belgeTarihi") or "")} for k in kayitlar if not str(k.get("mukellefUnvan") or "").strip()]
    istek = [x for x in istek if x["aliciVknTckn"]]
    if not istek:
        return {"yazilan": 0, "bulunamayan": [], "hatalar": []}
    return fr.evaluate("""async ([liste, sid]) => {
        const out = {yazilan: 0, bulunamayan: [], hatalar: []}, unvanlar = {};
        const bekle = ms => new Promise(r => setTimeout(r, ms));
        const sorgula = vkn => new Promise((tamam, hata) => $j.ajax({
            url: "cariAdresSorgulamaAction.do?islem=seciliSorgula&tcNo=" + vkn + "&sirketId=" + sid + "&r=" + Math.random(),
            success: tamam, error: (x, s, e) => hata(e || s)}));
        for (const k of liste) {
            let unvan = unvanlar[k.aliciVknTckn];
            if (unvan === undefined) {
                try {
                    const ret = await sorgula(k.aliciVknTckn);
                    const res = eval("(" + String(ret).replace(/\\r?\\n/g, " ").replace(/\\s+/g, " ") + ")");   // ekranın yaptığı gibi
                    if (res.hata && res.hata !== "null") { out.hatalar.push(k.aliciVknTckn + ": " + res.hata); unvan = ""; }
                    else unvan = (res.unvan || "").trim();
                } catch (e) { out.hatalar.push(k.aliciVknTckn + ": " + e); unvan = ""; }
                unvanlar[k.aliciVknTckn] = unvan;
                if (!unvan && out.bulunamayan.indexOf(k.aliciVknTckn) < 0) out.bulunamayan.push(k.aliciVknTckn);
            }
            if (!unvan) continue;
            const r = await fetchData("gib530/unvan_kaydet.jq", Object.assign({}, k, {sirket_id: sid, tur: "utts", aliciUnvanAdSoyad: unvan}));
            if (r && r.success) out.yazilan++;
            else out.hatalar.push(k.belgeNumarasi + ": " + ((r && (r.error || r.warning)) || "belirsiz yanıt"));
            await bekle(300);
        }
        return out;
    }""", [istek, str(sirket_id)])


def utts_excel(ctx, pg, kayitlar):
    """Ekranın Excel çıktısı (excel_utts ile aynı alanlar). Döner: dosya baytları."""
    alanlar = ("ettn", "uttsFisNo", "mukellefVknTckn", "muhasebeciVkn", "plaka", "satisTipi", "odemeTipi", "belgeTarihi", "odemeTutar",
               "birimTutar", "miktar", "yakitAdi", "matrah", "vergiTutari", "vergiOrani", "toplamTutar")
    veri = []
    for k in kayitlar:
        x = {a: k.get(a) for a in alanlar}
        x["aracTipi"] = "Binek" if str(k.get("aracTipi")) == "1" else "Diğer"
        x["mukellefUnvan"] = k.get("mukellefUnvan") or ""
        veri.append(x)
    r = _json_post(ctx, pg, "gib530/akaryakit_excel.jq", {"data": veri}, UTTS_SAYFA)
    if not isinstance(r, dict) or not r.get("uid"):
        raise LucaHatasi("UTTS Excel hazırlanamadı: " + str((r or {}).get("warning") or (r or {}).get("error") or r)[:200])
    d = ctx.request.get(_taban(pg) + "gib530/excel.jq?uid=" + str(r["uid"]), headers=_baslik(pg, UTTS_SAYFA), timeout=120_000)
    if d.status != 200 or not d.body():
        raise LucaHatasi(f"UTTS Excel indirilemedi (HTTP {d.status}).")
    return d.body()

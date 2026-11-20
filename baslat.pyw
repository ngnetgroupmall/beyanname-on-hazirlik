"""Beyanname Ön Hazırlık — taşınabilir paketin başlatıcısı (pythonw ile çalışır; konsol yoktur).
Sıra: tek kopya kilidi -> güncelleme denetimi (guncelle.py; dosyalar değiştiyse süreç yeniden başlar) -> program.
Açılış hatasını (eksik dosya, içe aktarma hatası…) %LOCALAPPDATA%\\KdvOnHazirlik\\baslatma_hatasi.txt dosyasına yazar ve ileti
kutusuyla gösterir; hata yeni inen bir güncellemeden kaynaklanıyorsa önceki sürüme döner.
Geliştirme bilgisayarında gerekmez ("Beyanname On Hazirlik.bat" doğrudan onhazirlik.py'yi çalıştırır)."""
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

KLASOR = Path(__file__).resolve().parent
if str(KLASOR) not in sys.path:
    sys.path.insert(0, str(KLASOR))
AD = "Beyanname Ön Hazırlık"
SAYAC = "BOH_YENIDEN"                           # art arda yeniden başlatma sayısı (sonsuz döngü olmasın)
BILGI = "BOH_GUNCELLEME_BILGISI"                # yeniden başlayan sürece aktarılan günlük satırı


def _kutu(metin, simge=0x10):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, metin, AD, simge)
    except Exception:
        pass


def _hata(metin):
    veri = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KdvOnHazirlik"
    try:
        veri.mkdir(parents=True, exist_ok=True)
        (veri / "baslatma_hatasi.txt").write_text(f"{datetime.now():%d.%m.%Y %H:%M:%S}\n{metin}", encoding="utf-8")
    except OSError:
        pass
    _kutu("Program açılamadı.\n\n" + metin.strip()[-700:] + f"\n\nAyrıntı: {veri}\\baslatma_hatasi.txt")


def _yeniden_baslat(kilit, bilgi=""):
    """Dosyalar değişti: temiz bir süreç açılır (bellekteki eski modüllerle sürülmez). Açılamazsa False."""
    n = int(os.environ.get(SAYAC) or 0)
    if n >= 3:
        return False
    ortam = dict(os.environ, **{SAYAC: str(n + 1), BILGI: bilgi})
    try:
        if kilit is not None:
            kilit.close()                           # yeni süreç kilidi alabilsin
        subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve())], env=ortam, cwd=str(KLASOR))
    except Exception:
        return False
    return True


def calistir():
    try:
        import guncelle
    except Exception:
        guncelle = None                             # güncelleyici bozuksa program yine açılır
    kilit = guncelle.kilit_al() if guncelle else None
    if guncelle and kilit is None:
        _kutu("Program zaten açık.", 0x40)
        return
    bilgi = os.environ.get(BILGI) or ""
    uyari = ""
    if guncelle:
        s = guncelle.baslangic()
        uyari = s.get("uyari") or ""
        if s.get("yeniden") and _yeniden_baslat(kilit, s.get("mesaj") or ""):
            return
        bilgi = bilgi or s.get("mesaj") or ""
    try:
        import onhazirlik
        onhazirlik.main(kilit=kilit, acilis_bilgisi=bilgi, acilis_uyarisi=uyari)
    except SystemExit:
        raise
    except BaseException:
        iz = traceback.format_exc()
        geri = None
        try:
            if guncelle and guncelle.bekleyen_var():    # yeni inen sürüm açılamadı: önceki sürüme dön
                geri = guncelle.geri_al()
        except Exception:
            pass
        if geri and _yeniden_baslat(kilit, f"Yeni sürüm ({geri}) açılamadı; önceki sürüme dönüldü."):
            return
        _hata(iz)
        sys.exit(1)


calistir()

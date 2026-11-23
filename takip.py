# -*- coding: utf-8 -*-
"""Beyanname indirilince Beyanname Takip panelini hemen günceller (kullanıcı isteği 21.11.2026).

Programın bir üst klasöründe scripts\\takip_otomatik_gorev.py varsa (yalnız bu ofisin bilgisayarı) onu arka planda,
penceresiz başlatır; o da değişen firma-dönemleri panele yükler. Dosya yoksa (program başka ofise dağıtılmışsa) hiçbir şey
yapmaz. Hata programı durdurmaz; yalnız günlüğe yazılır. Sonuç scripts\\takip_otomatik.log dosyasındadır."""
import subprocess
import sys
from pathlib import Path

GOREV = Path(__file__).resolve().parent.parent / "scripts" / "takip_otomatik_gorev.py"


def tetikle(gunluk=print):
    if not GOREV.is_file():
        return False
    try:
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        komut = [str(pyw if pyw.is_file() else exe), "-I", "-X", "utf8", str(GOREV)]
        bayrak = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(komut, cwd=str(GOREV.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=bayrak, close_fds=True)
        gunluk("    Beyanname Takip paneli güncelleniyor (arka planda; birkaç saniye sürer).")
        return True
    except Exception as e:      # takip güncellenemese de indirme başarılıdır
        gunluk(f"    UYARI: Beyanname Takip güncellemesi başlatılamadı: {e}")
        return False

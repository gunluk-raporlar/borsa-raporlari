"""Kullanim defterini OPS deposuna (private) senkronlar.

OPS_REPO_TOKEN secret'i yoksa SESSIZCE ATLANIR (normal durum: token
eklenene dek ana repodaki kopya yeter). Varsayilan hedef:
gunluk-raporlar/borsa-raporlari-ops (private, 2026-10-08).

Cagrilan yerler: daily.yml / makro-analiz.yml / derin-analiz.yml
"Degisiklikleri yayinla" adimindan sonra.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DOSYALAR = [Path("data/kullanim-defteri.csv"), Path("data/kullanim-defteri.xlsx")]


def _cal(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print("[OpsSync] basarisiz:", " ".join(cmd[:3]), "...",
              "\n", (r.stderr or r.stdout)[-400:])
        return False
    return True


def main():
    token = os.environ.get("OPS_REPO_TOKEN", "").strip()
    repo = os.environ.get("OPS_REPO", "gunluk-raporlar/borsa-raporlari-ops")
    if not token:
        print("[OpsSync] OPS_REPO_TOKEN yok — senkron atlandi "
              "(secret eklenince otomatik devreye girer).")
        return 0
    mevcut = [d for d in DOSYALAR if d.exists()]
    if not mevcut:
        print("[OpsSync] defter dosyalari yok — senkron atlandi.")
        return 0

    git = shutil.which("git")
    tmp = tempfile.mkdtemp(prefix="ops-sync-")
    try:
        if not _cal([git, "clone", "--depth", "1",
                     f"https://x-access-token:{token}@github.com/{repo}.git", tmp]):
            return 1
        for d in mevcut:
            hedef = Path(tmp) / "kullanim-defteri" / d.name
            hedef.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(d, hedef)
        if not _cal([git, "add", "-A"], cwd=tmp):
            return 1
        durum = subprocess.run([git, "status", "--porcelain"], cwd=tmp,
                               capture_output=True, text=True)
        if not durum.stdout.strip():
            print("[OpsSync] degisiklik yok — push atlandi.")
            return 0
        if not _cal([git, "-c", "user.name=borsa-bot",
                     "-c", "user.email=borsa-bot@users.noreply.github.com",
                     "commit", "-m", "Kullanim defteri guncelleme"], cwd=tmp):
            return 1
        if not _cal([git, "push", "origin", "HEAD"], cwd=tmp):
            return 1
        print(f"[OpsSync] OK: {len(mevcut)} dosya {repo} (private) deposuna islendi.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

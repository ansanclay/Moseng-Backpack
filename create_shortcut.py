# -*- coding: utf-8 -*-
"""
Moseng Backpack — 바탕화면 바로가기 설치 스크립트
  python create_shortcut.py

실행하면:
  1. backpack/ui/resources/icon.ico 생성 (Pillow 필요)
  2. 바탕화면에 'Moseng Backpack.lnk' 생성
  3. 바로가기 우클릭 → '작업 표시줄에 고정' 으로 핀 가능
"""

import os
import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


# ── 1. 아이콘 생성 ────────────────────────────────────────────────────────────

def _make_icon() -> Path | None:
    ico_path = HERE / "backpack" / "ui" / "resources" / "icon.ico"
    if ico_path.exists():
        print(f"아이콘 이미 존재: {ico_path}")
        return ico_path

    try:
        from PIL import Image, ImageDraw

        sizes = [256, 128, 64, 48, 32, 16]
        frames = []

        for sz in sizes:
            img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
            d   = ImageDraw.Draw(img)

            r = max(3, sz // 6)
            # 배경 — 어두운 둥근 사각형
            d.rounded_rectangle([0, 0, sz - 1, sz - 1], radius=r,
                                 fill=(22, 24, 30, 255))

            pad = max(2, sz // 8)
            # 배낭 몸체 (파란색)
            body_top = sz // 3
            d.rounded_rectangle(
                [pad, body_top, sz - pad, sz - pad],
                radius=max(2, sz // 10),
                fill=(0, 42, 255, 255),
            )
            # 배낭 덮개 (연한 파란)
            d.rounded_rectangle(
                [pad * 2, pad, sz - pad * 2, body_top + sz // 12],
                radius=max(2, sz // 12),
                fill=(51, 102, 255, 230),
            )
            # 어깨끈
            sw  = max(2, sz // 14)
            mid = sz // 2
            d.rectangle(
                [mid - sw, pad + sz // 14, mid + sw, body_top],
                fill=(200, 225, 255, 200),
            )
            # 잠금 버클 점
            bk = max(2, sz // 16)
            d.ellipse(
                [mid - bk, body_top + sz // 8 - bk,
                 mid + bk, body_top + sz // 8 + bk],
                fill=(255, 255, 255, 200),
            )
            frames.append(img)

        ico_path.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            ico_path,
            format="ICO",
            sizes=[(s, s) for s in sizes],
            append_images=frames[1:],
        )
        print(f"아이콘 생성 완료: {ico_path}")
        return ico_path

    except ImportError:
        print("Pillow 미설치 — 기본 Python 아이콘 사용 (pip install pillow 로 개선 가능)")
        return None
    except Exception as e:
        print(f"아이콘 생성 실패 ({e}) — 기본 아이콘 사용")
        return None


# ── 2. .lnk 바로가기 생성 ─────────────────────────────────────────────────────

def _make_shortcut(ico_path: Path | None) -> None:
    # pythonw.exe = 콘솔 창 없이 실행
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable)

    target  = str(pythonw)
    args    = f'"{HERE / "main.py"}"'
    workdir = str(HERE)
    icon    = str(ico_path) if ico_path else str(pythonw)

    desktop  = Path(os.path.expandvars(r"%USERPROFILE%")) / "Desktop"
    lnk_path = desktop / "Moseng Backpack.lnk"

    # PowerShell WScript.Shell 로 .lnk 생성 (순수 Windows 내장 기능)
    ps_script = f"""
$s   = New-Object -ComObject WScript.Shell
$lnk = $s.CreateShortcut('{str(lnk_path).replace("'", "''")}')
$lnk.TargetPath      = '{target.replace("'", "''")}'
$lnk.Arguments       = '{args.replace("'", "''")}'
$lnk.WorkingDirectory= '{workdir.replace("'", "''")}'
$lnk.IconLocation    = '{icon.replace("'", "''")}',0
$lnk.Description     = 'Moseng Backpack Asset Manager'
$lnk.WindowStyle     = 1
$lnk.Save()
"""

    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True, text=True,
    )

    if result.returncode == 0:
        print(f"\n[OK] 바로가기 생성 완료: {lnk_path}")
        print("\n-- 작업표시줄에 고정하는 방법 --")
        print("  바탕화면의 'Moseng Backpack' 바로가기를 우클릭")
        print("  -> '작업 표시줄에 고정' 클릭")
    else:
        print(f"바로가기 생성 실패:\n{result.stderr}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Moseng Backpack 바로가기 설치 ===\n")
    ico = _make_icon()
    _make_shortcut(ico)
    print("\n완료.")
    input("\n계속하려면 Enter 키를 누르세요...")

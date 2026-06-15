"""
빌드 스크립트 — PyInstaller로 단일 EXE 생성
사용법: python build.py
"""

import subprocess
import sys
import os

APP_NAME   = "Train_copy"
MAIN_SCRIPT = "Train_copy.py"

def install_pyinstaller():
    """PyInstaller가 없으면 자동 설치"""
    try:
        import PyInstaller
    except ImportError:
        print("[build] PyInstaller 설치 중...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

def build():
    install_pyinstaller()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",            # 단일 EXE
        "--windowed",           # 콘솔 창 숨김 (GUI 전용)
        "--clean",              # 이전 빌드 캐시 제거
        f"--name={APP_NAME}",
        # 필요 시 아이콘 추가: f"--icon=icon.ico",
        MAIN_SCRIPT,
    ]

    print(f"[build] 명령어: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        exe_path = os.path.join("dist", f"{APP_NAME}.exe")
        print(f"\n✅ 빌드 성공: {exe_path}")
    else:
        print("\n❌ 빌드 실패 — 위 오류 메시지를 확인하세요.")
        sys.exit(1)

if __name__ == "__main__":
    build()

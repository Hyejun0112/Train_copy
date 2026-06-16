"""
빌드 스크립트 — PyInstaller로 단일 EXE 생성
사용법: python build.py
"""

import subprocess
import sys
import os

APP_NAME    = "Train_copy"
MAIN_SCRIPT = "Train_copy.py"

HIDDEN_IMPORTS = [
    # PyMuPDF (fitz)
    "fitz", "fitz.fitz",
    # openpyxl
    "openpyxl",
    "openpyxl.styles", "openpyxl.utils", "openpyxl.workbook",
    "openpyxl.reader.excel", "openpyxl.writer.excel",
    # pyautogui / pygetwindow
    "pyautogui", "pygetwindow",
    "pyscreeze", "pymsgbox", "pytweening",
    # tkinter (보통 자동 포함되지만 명시)
    "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
]

def install_pyinstaller():
    try:
        import PyInstaller
    except ImportError:
        print("[build] PyInstaller 설치 중...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

def check_dependencies():
    missing = []
    for pkg in ["fitz", "openpyxl", "pyautogui", "pygetwindow"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[build] 누락된 패키지 설치 중: {missing}")
        pip_names = {"fitz": "PyMuPDF"}
        for pkg in missing:
            pip_name = pip_names.get(pkg, pkg)
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])

def build():
    install_pyinstaller()
    check_dependencies()

    hidden_args = []
    for imp in HIDDEN_IMPORTS:
        hidden_args += ["--hidden-import", imp]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",       # 단일 EXE (배포 편의)
        "--windowed",      # 콘솔 창 숨김
        "--clean",         # 이전 빌드 캐시 제거
        f"--name={APP_NAME}",
        *hidden_args,
        # PyMuPDF 내부 바이너리 수집
        "--collect-all", "fitz",
        # openpyxl 템플릿 파일 수집
        "--collect-data", "openpyxl",
        MAIN_SCRIPT,
    ]

    print(f"[build] 빌드 시작...\n")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        exe_path = os.path.join("dist", f"{APP_NAME}.exe")
        print(f"\n✅ 빌드 성공: {exe_path}")
        print(f"   배포 시 dist\\{APP_NAME}.exe 파일 하나만 전달하면 됩니다.")
    else:
        print("\n❌ 빌드 실패 — 위 오류 메시지를 확인하세요.")
        sys.exit(1)

if __name__ == "__main__":
    build()

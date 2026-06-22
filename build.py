"""
배포용 빌드 스크립트 — PyInstaller로 단일 EXE 생성
사용법: python build.py

배포 EXE에서 흔한 오류를 미리 차단한다:
  - multiprocessing(spawn) 무한 재실행  → Train_copy.py에 freeze_support() 존재
  - PyMuPDF(fitz) 네이티브 바이너리 누락 → collect-all fitz
  - openpyxl 데이터 누락                → collect-data openpyxl
완성 산출물: dist/Train_copy.exe (이 파일 하나만 배포하면 됨)
"""

import subprocess
import sys
import os

APP_NAME    = "Train_copy"
MAIN_SCRIPT = "Train_copy.py"

# 런타임에 필요한데 PyInstaller가 자동으로 못 찾을 수 있는 모듈들
HIDDEN_IMPORTS = [
    "fitz", "fitz.fitz",
    "openpyxl", "openpyxl.styles", "openpyxl.utils", "openpyxl.workbook",
    "openpyxl.reader.excel", "openpyxl.writer.excel",
    "pyautogui", "pygetwindow", "pyscreeze", "pymsgbox", "pytweening",
    "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
]


def _pip_install(pip_name):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])


def install_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] PyInstaller 설치 중...")
        _pip_install("pyinstaller")


def check_dependencies():
    """런타임 의존성이 빌드 환경에 설치돼 있는지 확인하고, 없으면 설치한다.
    (설치 안 된 채 빌드하면 EXE 실행 시 ModuleNotFoundError가 난다.)"""
    pip_names = {"fitz": "PyMuPDF"}
    for pkg in ["fitz", "openpyxl", "pyautogui", "pygetwindow"]:
        try:
            __import__(pkg)
        except ImportError:
            name = pip_names.get(pkg, pkg)
            print(f"[build] 누락된 패키지 설치 중: {name}")
            _pip_install(name)


def verify_freeze_support():
    """multiprocessing(spawn) 사용 시 freeze_support()가 없으면 onefile EXE가
    무한 재실행된다. 메인 스크립트에 들어있는지 사전 점검."""
    try:
        with open(MAIN_SCRIPT, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return
    if "freeze_support()" not in src:
        print("⚠ [build] 경고: Train_copy.py에 multiprocessing.freeze_support() 가 "
              "없습니다. onefile EXE가 무한 재실행될 수 있습니다.")


def build():
    install_pyinstaller()
    check_dependencies()
    verify_freeze_support()

    hidden_args = []
    for imp in HIDDEN_IMPORTS:
        hidden_args += ["--hidden-import", imp]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",        # 단일 EXE (배포 편의)
        "--windowed",       # 콘솔 창 숨김(GUI 앱)
        "--clean",          # 이전 빌드 캐시 제거
        "--noconfirm",      # dist 덮어쓸 때 확인 안 물음(자동화)
        f"--name={APP_NAME}",
        *hidden_args,
        "--collect-all", "fitz",        # PyMuPDF 네이티브 바이너리 전부 수집
        "--collect-data", "openpyxl",   # openpyxl 템플릿 데이터 수집
        MAIN_SCRIPT,
    ]

    print("[build] 빌드 시작...\n")
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

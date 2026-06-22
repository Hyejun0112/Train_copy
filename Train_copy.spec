# -*- mode: python ; coding: utf-8 -*-
# 빌드: pyinstaller Train_copy.spec   (또는 python build.py)
# PyMuPDF(fitz) 바이너리와 openpyxl 데이터를 빠짐없이 수집해 배포 EXE에서
# "모듈/바이너리 없음" 오류가 나지 않도록 구성한다.

from PyInstaller.utils.hooks import collect_all, collect_data_files

# PyMuPDF 네이티브 바이너리/데이터/서브모듈 전부 수집
fitz_datas, fitz_binaries, fitz_hidden = collect_all('fitz')

hiddenimports = fitz_hidden + [
    'fitz', 'fitz.fitz',
    'openpyxl', 'openpyxl.styles', 'openpyxl.utils', 'openpyxl.workbook',
    'openpyxl.reader.excel', 'openpyxl.writer.excel',
    'pyautogui', 'pygetwindow', 'pyscreeze', 'pymsgbox', 'pytweening',
    'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox',
]

datas = fitz_datas + collect_data_files('openpyxl')

a = Analysis(
    ['Train_copy.py'],
    pathex=[],
    binaries=fitz_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Train_copy',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

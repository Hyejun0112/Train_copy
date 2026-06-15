import os
import shutil
import tempfile
import time
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pyautogui
import pygetwindow as gw

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# ── 전역 설정 ──────────────────────────────────────────────
WAIT_OPEN  = 5
WAIT_SHORT = 0.8
WAIT_PASTE = 2.5

# ── 상태 변수 ──────────────────────────────────────────────
src_folder    = ""
dst_folder    = ""
output_folder = ""
src_files     = []
dst_files     = []
mapping       = []
stop_flag     = False

# 마크업 필터 설정
filter_enabled   = False
filter_color     = ""   # RGB hex, 예: "0000FF"
filter_date_from = ""   # "YYYY-MM-DD"
filter_date_to   = ""   # "YYYY-MM-DD"
filter_author    = ""   # 작성자(사번)



# ══════════════════════════════════════════════════════════
#  Bluebeam 제어 유틸
# ══════════════════════════════════════════════════════════

def focus_bluebeam(timeout: int = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        wins = gw.getWindowsWithTitle("Bluebeam")
        if wins:
            try:
                wins[0].activate()
                time.sleep(0.8)
                return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def fit_page():
    pyautogui.hotkey('ctrl', 'shift', 'h')
    time.sleep(WAIT_SHORT)


def open_pdf(path: str):
    os.startfile(path)
    time.sleep(WAIT_OPEN)
    focus_bluebeam()
    fit_page()


def close_pdf_discard():
    """저장 없이 닫기 — 저장 여부 팝업이 뜨면 N으로 닫음"""
    pyautogui.hotkey('ctrl', 'w')
    time.sleep(WAIT_SHORT)
    pyautogui.press('n')
    time.sleep(WAIT_SHORT)


# ══════════════════════════════════════════════════════════
#  핵심 작업 로직
# ══════════════════════════════════════════════════════════

class StoppedError(Exception):
    pass


def _parse_pdf_date(date_str):
    """PDF 날짜 형식 'D:YYYYMMDDHHMMSS...' → 'YYYY-MM-DD' (실패 시 None)"""
    if not date_str:
        return None
    s = date_str.lstrip("D:")
    if len(s) < 8:
        return None
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _annot_matches_filter(annot, color_hex, date_from, date_to, author):
    """주어진 조건을 모두 만족해야 True (조건이 비어있으면 해당 항목은 통과)"""
    if color_hex:
        stroke = (annot.colors or {}).get("stroke")
        if not stroke:
            return False
        target = tuple(int(color_hex[i:i + 2], 16) / 255 for i in (0, 2, 4))
        if any(abs(a - b) > 0.08 for a, b in zip(stroke, target)):
            return False

    if author:
        annot_author = (annot.info.get("title") or "").strip()
        if annot_author.lower() != author.strip().lower():
            return False

    if date_from or date_to:
        d = _parse_pdf_date(annot.info.get("modDate") or annot.info.get("creationDate"))
        if d is None:
            return False
        if date_from and d < date_from:
            return False
        if date_to and d > date_to:
            return False

    return True


def build_filtered_copy(src_path, color_hex, date_from, date_to, author, log_fn=None):
    """필터 조건에 맞지 않는 마크업(annotation)을 제거한 임시 PDF를 만들어
    그 경로를 반환한다. fitz(PyMuPDF)가 없으면 RuntimeError."""
    if fitz is None:
        raise RuntimeError("PyMuPDF(fitz)가 설치되어 있지 않습니다. 'pip install PyMuPDF' 필요")

    doc = fitz.open(src_path)
    kept, removed = 0, 0
    for page in doc:
        # 1단계: 읽기 전용으로 삭제 대상 xref만 수집 (delete 중 객체 무효화 방지)
        delete_xrefs = []
        for annot in page.annots() or []:
            if _annot_matches_filter(annot, color_hex, date_from, date_to, author):
                kept += 1
            else:
                delete_xrefs.append(annot.xref)

        # 2단계: xref로 다시 로드해서 한 개씩 삭제
        for xref in delete_xrefs:
            annot = page.load_annot(xref)
            page.delete_annot(annot)
            removed += 1

    if log_fn:
        log_fn(f"  [필터] 유지 {kept}개 / 제외 {removed}개\n")

    fd, tmp_path = tempfile.mkstemp(
        suffix=".pdf", prefix="_filtered_", dir=os.path.dirname(src_path) or None
    )
    os.close(fd)
    doc.save(tmp_path)
    doc.close()
    return tmp_path


def process_pair(src: str, dst: str, out: str, log_fn=None, stop_check=None,
                 filter_settings=None):
    """
    1) src 열기 → 마크업 전체 복사
    2) src 닫기
    3) dst를 output 폴더로 먼저 복사 (shutil) → 복사본 열기
    4) Paste in Place (Ctrl+Shift+V)
    5) Ctrl+S 로 저장 (Save — 같은 파일명 덮어쓰기)
    6) 닫기

    ★ 저장을 'Save As' 다이얼로그 없이 Ctrl+S 로 처리
      → 파일명 입력 오류 원천 차단
    """
    def log(msg):
        if log_fn:
            log_fn(msg)

    def check_stop():
        if stop_check and stop_check():
            raise StoppedError()

    # ── 1) Source 열기 → 복사
    check_stop()

    open_src = src
    if filter_settings:
        log("  [필터] 마크업 필터 적용 중…\n")
        open_src = build_filtered_copy(
            src,
            filter_settings.get("color", ""),
            filter_settings.get("date_from", ""),
            filter_settings.get("date_to", ""),
            filter_settings.get("author", ""),
            log_fn=log,
        )

    log(f"  [1/4] Source 열기: {os.path.basename(src)}\n")
    open_pdf(open_src)
    fit_page()
    time.sleep(WAIT_SHORT)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(WAIT_SHORT)
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(WAIT_SHORT)

    log("  [2/4] 마크업 복사 완료 → Source 닫기\n")
    close_pdf_discard()

    if open_src != src:
        try:
            os.remove(open_src)
        except OSError:
            pass

    check_stop()

    # ── 2) Target → Output 폴더에 미리 복사 (shutil 직접 복사)
    log(f"  [3/4] Output 복사: {os.path.basename(out)}\n")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copy2(dst, out)   # dst 원본 → output 폴더 복사본 생성
    time.sleep(0.5)

    # ── 3) 복사본(output) 열기 → Paste in Place → Ctrl+S 저장
    log(f"  [4/4] Output 파일 열기 → 마크업 붙여넣기 → 저장\n")
    open_pdf(out)
    fit_page()
    time.sleep(WAIT_SHORT)
    check_stop()

    pyautogui.hotkey('ctrl', 'shift', 'v')   # Paste in Place
    time.sleep(WAIT_PASTE)

    pyautogui.hotkey('ctrl', 's')            # Save (덮어쓰기)
    time.sleep(2.0)
    pyautogui.press('enter')                 # 덮어쓰기 확인 팝업 대비
    time.sleep(1.0)

    close_pdf_discard()
    log("  ✓ 완료\n")


# ══════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Bluebeam Markup Auto-Copy")
        self.resizable(True, True)
        self.minsize(900, 640)
        self._build_ui()

    # ── UI 최상위 ──────────────────────────────────────────

    def _build_ui(self):
        self.configure(bg="#1e1e2e")
        self._build_header()
        self._build_body()
        self._build_footer()

    def _build_header(self):
        hdr = tk.Frame(self, bg="#313244", pady=10)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text="🔵  Bluebeam Markup Auto-Copy",
            font=("Segoe UI", 16, "bold"),
            fg="#cdd6f4", bg="#313244"
        ).pack()
        tk.Label(
            hdr, text="P&ID 마크업을 동일 Train 도면에 자동 복사",
            font=("Segoe UI", 9), fg="#a6adc8", bg="#313244"
        ).pack()

    def _build_body(self):
        body = tk.Frame(self, bg="#1e1e2e")
        body.pack(fill="both", expand=True, padx=14, pady=10)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=4)
        body.rowconfigure(0, weight=1)
        self._build_left(body)
        self._build_right(body)

    def _build_footer(self):
        footer = tk.Frame(self, bg="#181825", pady=8)
        footer.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(footer, mode="determinate")
        self.progress.pack(fill="x", padx=14, pady=(0, 4))
        self.lbl_status = tk.Label(
            footer, text="대기 중",
            font=("Segoe UI", 9), fg="#a6adc8", bg="#181825"
        )
        self.lbl_status.pack()

    # ── 왼쪽 패널 ──────────────────────────────────────────

    def _build_left(self, parent):
        left = tk.Frame(parent, bg="#1e1e2e")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(4, weight=1)   # 파일 목록 영역 확장
        left.columnconfigure(0, weight=1)

        # 폴더 설정 섹션
        self._sec(left, "📁 폴더 설정", row=0)

        folder_frm = tk.Frame(left, bg="#1e1e2e")
        folder_frm.grid(row=1, column=0, sticky="ew")
        folder_frm.columnconfigure(0, weight=1)

        self.lbl_src = self._folder_row(folder_frm, 0, "Source 폴더",
                                        self._select_source, "#a6e3a1")
        self.lbl_dst = self._folder_row(folder_frm, 1, "Target 폴더",
                                        self._select_target, "#89b4fa")
        self.lbl_out = self._folder_row(folder_frm, 2, "Output 폴더",
                                        self._select_output, "#f9e2af")

        # 파일 목록 섹션
        self._sec(left, "📄 파일 목록", row=2)

        # 탭으로 src / dst / out 파일 표시
        notebook = ttk.Notebook(left)
        notebook.grid(row=3, column=0, sticky="nsew", pady=(0, 6))
        left.rowconfigure(3, weight=1)

        nb_style = ttk.Style()
        nb_style.theme_use("clam")
        nb_style.configure("TNotebook",        background="#1e1e2e", borderwidth=0)
        nb_style.configure("TNotebook.Tab",    background="#313244", foreground="#a6adc8",
                           padding=[8, 4], font=("Segoe UI", 8))
        nb_style.map("TNotebook.Tab",
                     background=[("selected", "#45475a")],
                     foreground=[("selected", "#cdd6f4")])

        self.notebook = notebook
        self.list_src = self._file_listbox(notebook, "Source")
        self.list_dst = self._file_listbox(notebook, "Target")
        self.list_out = self._file_listbox(notebook, "Output")

        # 매핑 + 실행 섹션
        self._sec(left, "🔗 매핑 / 실행", row=4)
        left.rowconfigure(4, weight=0)

        btn_frm = tk.Frame(left, bg="#1e1e2e")
        btn_frm.grid(row=5, column=0, sticky="ew", pady=(0, 4))
        btn_frm.columnconfigure(0, weight=1)
        btn_frm.columnconfigure(1, weight=1)
        btn_frm.columnconfigure(2, weight=1)

        tk.Button(btn_frm, text="매핑 편집",
                  command=self._open_mapping,
                  **self._bkw("#7f849c")
                  ).grid(row=0, column=0, sticky="ew", padx=2)

        tk.Button(btn_frm, text="🚀 실행",
                  command=self._start_run,
                  **self._bkw("#a6e3a1", fg="#1e1e2e", bold=True)
                  ).grid(row=0, column=1, sticky="ew", padx=2)

        tk.Button(btn_frm, text="⏹ 중지",
                  command=self._stop,
                  **self._bkw("#f38ba8", fg="#1e1e2e")
                  ).grid(row=0, column=2, sticky="ew", padx=2)

        self.lbl_mapping = tk.Label(
            left, text="매핑: 0 쌍",
            font=("Segoe UI", 9), fg="#a6adc8", bg="#1e1e2e"
        )
        self.lbl_mapping.grid(row=6, column=0, sticky="w")

        # 마크업 필터 섹션
        self._sec(left, "🎯 마크업 필터 (선택)", row=7)
        self._build_filter_section(left, row=8)

    def _build_filter_section(self, parent, row: int):
        frm = tk.Frame(parent, bg="#1e1e2e")
        frm.grid(row=row, column=0, sticky="ew", pady=(2, 0))
        frm.columnconfigure(1, weight=1)

        self.var_filter_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(
            frm, text="필터 사용 (특정 마크업만 붙여넣기)",
            variable=self.var_filter_enabled,
            font=("Segoe UI", 9), fg="#a6adc8", bg="#1e1e2e",
            selectcolor="#313244", activebackground="#1e1e2e",
            activeforeground="#cdd6f4"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(2, 4))

        def _entry_row(r, label, width=24):
            tk.Label(frm, text=label, font=("Segoe UI", 8),
                     fg="#6c7086", bg="#1e1e2e", anchor="w", width=10
                     ).grid(row=r, column=0, sticky="w")
            e = tk.Entry(frm, width=width, bg="#181825", fg="#cdd6f4",
                          insertbackground="#cdd6f4", relief="flat")
            e.grid(row=r, column=1, sticky="ew", padx=4, pady=1)
            return e

        # 색상 — Bluebeam 표준 마크업 색상 팔레트
        tk.Label(frm, text="색상", font=("Segoe UI", 8),
                 fg="#6c7086", bg="#1e1e2e", anchor="nw", width=10
                 ).grid(row=1, column=0, sticky="nw")
        self.var_filter_color = tk.StringVar(value="")
        self._build_color_palette(frm).grid(row=1, column=1, sticky="w", pady=1)

        self.ent_date_from = _entry_row(2, "기간 시작")
        self.ent_date_to   = _entry_row(3, "기간 종료")
        self.ent_author    = _entry_row(4, "작성자(사번)")

        tk.Label(
            frm,
            text="※ 색상: 아래 팔레트에서 선택 (다시 클릭하면 선택 해제)\n"
                 "   기간: YYYY-MM-DD 형식\n"
                 "   조건을 만족하는 마크업만 Target에 붙여넣습니다.\n"
                 "   (비워두면 해당 조건은 무시)",
            font=("Segoe UI", 8), fg="#6c7086", bg="#1e1e2e",
            justify="left", anchor="w"
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 2))

    # Bluebeam Revu 마크업 색상 팔레트 (RGB hex) — 10열 x 4행
    BLUEBEAM_PALETTE = [
        # 파스텔
        "FFCCCC", "FFE5CC", "FFFFCC", "CCFFCC", "CCFFFF",
        "CCE5FF", "CCCCFF", "E5CCFF", "FFCCFF", "FFCCE5",
        # 기본(밝은) 색
        "FF0000", "FF8000", "FFFF00", "00FF00", "00FFFF",
        "0080FF", "0000FF", "8000FF", "FF00FF", "FF0080",
        # 진한(어두운) 색
        "800000", "804000", "808000", "008000", "008080",
        "004080", "000080", "400080", "800080", "800040",
        # 그레이스케일
        "000000", "333333", "666666", "999999", "BBBBBB",
        "CCCCCC", "DDDDDD", "EEEEEE", "F5F5F5", "FFFFFF",
    ]

    def _build_color_palette(self, parent):
        frm = tk.Frame(parent, bg="#1e1e2e")
        self._palette_buttons = {}

        def _select(hex_color):
            current = self.var_filter_color.get()
            new_val = "" if current == hex_color else hex_color
            self.var_filter_color.set(new_val)
            for h, btn in self._palette_buttons.items():
                btn.configure(
                    relief="sunken" if h == new_val else "flat",
                    highlightbackground="#cdd6f4" if h == new_val else "#1e1e2e",
                    highlightthickness=2 if h == new_val else 1,
                )
            self.lbl_color_sel.config(
                text=f"선택됨: #{new_val}" if new_val else "선택됨: (전체)"
            )

        cols = 10
        for i, hex_color in enumerate(self.BLUEBEAM_PALETTE):
            r, c = divmod(i, cols)
            btn = tk.Button(
                frm, bg=f"#{hex_color}", width=2, height=1,
                relief="flat", bd=0, highlightthickness=1,
                highlightbackground="#1e1e2e",
                command=lambda h=hex_color: _select(h)
            )
            btn.grid(row=r, column=c, padx=2, pady=2)
            self._palette_buttons[hex_color] = btn

        self.lbl_color_sel = tk.Label(
            frm, text="선택됨: (전체)", font=("Segoe UI", 8),
            fg="#a6adc8", bg="#1e1e2e", anchor="w"
        )
        self.lbl_color_sel.grid(row=(len(self.BLUEBEAM_PALETTE) - 1) // cols + 1,
                                 column=0, columnspan=cols, sticky="w", pady=(2, 0))
        return frm

    def _file_listbox(self, notebook: ttk.Notebook, tab_name: str) -> tk.Listbox:
        """탭 내 스크롤 가능한 파일 목록 Listbox 생성"""
        frm = tk.Frame(notebook, bg="#181825")
        notebook.add(frm, text=tab_name)
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        lb = tk.Listbox(
            frm,
            bg="#181825", fg="#cdd6f4",
            selectbackground="#45475a",
            font=("Consolas", 8),
            bd=0, highlightthickness=0,
            activestyle="none",
            selectmode="extended"
        )
        lb.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(frm, orient="vertical", command=lb.yview)
        sb.grid(row=0, column=1, sticky="ns")
        lb.configure(yscrollcommand=sb.set)
        return lb

    # ── 오른쪽 패널 (로그) — grid 전용 ────────────────────

    def _build_right(self, parent):
        right = tk.Frame(parent, bg="#1e1e2e")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        lbl = tk.Label(right, text="📋 작업 로그",
                       font=("Segoe UI", 10, "bold"),
                       fg="#cba6f7", bg="#1e1e2e", anchor="w")
        lbl.grid(row=0, column=0, sticky="ew", pady=(10, 0))

        sep = tk.Frame(right, bg="#45475a", height=1)
        sep.grid(row=1, column=0, sticky="ew", pady=(2, 6))

        log_frame = tk.Frame(right, bg="#181825", bd=1, relief="sunken")
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            bg="#181825", fg="#cdd6f4",
            font=("Consolas", 9),
            state="disabled", wrap="word",
            bd=0, padx=6, pady=6
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=sb.set)

        btn_wrap = tk.Frame(right, bg="#1e1e2e")
        btn_wrap.grid(row=3, column=0, sticky="e", pady=(4, 0))
        tk.Button(btn_wrap, text="로그 지우기",
                  command=self._clear_log,
                  **self._bkw("#45475a", pady=3)).pack()

    # ── 공통 헬퍼 ──────────────────────────────────────────

    def _sec(self, parent, title: str, row: int):
        """grid 기반 섹션 제목 + 구분선"""
        tk.Label(parent, text=title,
                 font=("Segoe UI", 10, "bold"),
                 fg="#cba6f7", bg="#1e1e2e", anchor="w"
                 ).grid(row=row, column=0, sticky="ew", pady=(10, 0))

    def _folder_row(self, parent, row: int, label: str,
                    cmd, accent: str) -> tk.Label:
        frm = tk.Frame(parent, bg="#1e1e2e")
        frm.grid(row=row, column=0, sticky="ew", pady=2)
        frm.columnconfigure(1, weight=1)

        tk.Button(frm, text=label, command=cmd, width=13,
                  **self._bkw(accent, fg="#1e1e2e")
                  ).grid(row=0, column=0, sticky="w")

        lbl = tk.Label(frm, text="(미설정)",
                       font=("Segoe UI", 8), fg="#6c7086",
                       bg="#1e1e2e", anchor="w", wraplength=220)
        lbl.grid(row=0, column=1, sticky="ew", padx=6)
        return lbl

    @staticmethod
    def _bkw(bg: str, fg: str = "#cdd6f4",
             bold: bool = False, pady: int = 5) -> dict:
        return dict(
            bg=bg, fg=fg, relief="flat",
            font=("Segoe UI", 9, "bold" if bold else "normal"),
            activebackground=bg, activeforeground=fg,
            cursor="hand2", padx=8, pady=pady, bd=0
        )

    # ── 폴더 선택 ──────────────────────────────────────────

    def _select_source(self):
        global src_folder, src_files
        path = filedialog.askdirectory(title="Source 폴더 선택")
        if not path:
            return
        src_folder = path
        src_files = sorted(f for f in os.listdir(path) if f.lower().endswith(".pdf"))
        self.lbl_src.config(
            text=f"{os.path.basename(path)}  ({len(src_files)}개)", fg="#a6adc8"
        )
        self._update_listbox(self.list_src, src_files)
        self.notebook.select(0)
        self._log(f"[Source] {path}  ({len(src_files)}개)\n")
        self._refresh_mapping_label()

    def _select_target(self):
        global dst_folder, dst_files
        path = filedialog.askdirectory(title="Target 폴더 선택")
        if not path:
            return
        dst_folder = path
        dst_files = sorted(f for f in os.listdir(path) if f.lower().endswith(".pdf"))
        self.lbl_dst.config(
            text=f"{os.path.basename(path)}  ({len(dst_files)}개)", fg="#a6adc8"
        )
        self._update_listbox(self.list_dst, dst_files)
        self.notebook.select(1)
        self._log(f"[Target] {path}  ({len(dst_files)}개)\n")
        self._refresh_mapping_label()

    def _select_output(self):
        global output_folder
        path = filedialog.askdirectory(title="Output 폴더 선택")
        if not path:
            return
        output_folder = path
        # Output 폴더의 기존 PDF 표시
        out_files = sorted(f for f in os.listdir(path) if f.lower().endswith(".pdf"))
        self.lbl_out.config(text=os.path.basename(path), fg="#a6adc8")
        self._update_listbox(self.list_out, out_files)
        self.notebook.select(2)
        self._log(f"[Output] {path}\n")

    def _update_listbox(self, lb: tk.Listbox, files: list):
        lb.delete(0, "end")
        for i, f in enumerate(files, 1):
            lb.insert("end", f"  {i:>3}.  {f}")
        # 짝수 행 색상 구분
        for i in range(0, len(files), 2):
            lb.itemconfig(i, bg="#1e1e2e")
        for i in range(1, len(files), 2):
            lb.itemconfig(i, bg="#181825")

    # ── 매핑 편집 창 ───────────────────────────────────────

    def _open_mapping(self):
        global mapping
        if not src_files or not dst_files:
            messagebox.showwarning("폴더 미설정", "Source / Target 폴더를 먼저 선택하세요.")
            return

        # Source/Target 탭에서 파일을 선택해두면 그 파일들로만 매핑
        sel_src = [src_files[i] for i in self.list_src.curselection()]
        sel_dst = [dst_files[i] for i in self.list_dst.curselection()]
        use_src = sel_src or src_files
        use_dst = sel_dst or dst_files

        if len(use_src) != len(use_dst):
            messagebox.showwarning(
                "파일 수 불일치",
                f"Source {len(use_src)}개 ≠ Target {len(use_dst)}개\n"
                "매핑 창에서 직접 삭제/조정하세요."
            )

        if not mapping:
            mapping = list(zip(use_src, use_dst))

        win = tk.Toplevel(self)
        win.title("파일 매핑 편집")
        win.geometry("900x520")
        win.configure(bg="#1e1e2e")
        win.grab_set()

        tk.Label(
            win,
            text="우클릭 → 선택 행 삭제  |  확정 버튼을 눌러야 반영됩니다",
            font=("Segoe UI", 9), fg="#a6adc8", bg="#1e1e2e"
        ).pack(pady=(8, 2))

        style = ttk.Style(win)
        style.theme_use("clam")
        style.configure("Map.Treeview",
                        background="#181825", fieldbackground="#181825",
                        foreground="#cdd6f4", rowheight=24,
                        font=("Consolas", 9))
        style.configure("Map.Treeview.Heading",
                        background="#313244", foreground="#cba6f7",
                        font=("Segoe UI", 9, "bold"))

        frm = tk.Frame(win, bg="#1e1e2e")
        frm.pack(fill="both", expand=True, padx=12, pady=4)

        tree = ttk.Treeview(frm, style="Map.Treeview",
                            columns=("#", "src", "dst"), show="headings")
        tree.heading("#",   text="#",      anchor="center")
        tree.heading("src", text="Source (마크업 원본)")
        tree.heading("dst", text="Target (붙여넣을 도면)")
        tree.column("#",   width=40, anchor="center", stretch=False)
        tree.column("src", width=390)
        tree.column("dst", width=390)

        vsb = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for i, (s, d) in enumerate(mapping, 1):
            tree.insert("", "end", values=(i, s, d))

        ctx = tk.Menu(win, tearoff=0, bg="#313244", fg="#cdd6f4",
                      activebackground="#45475a")

        def _delete():
            for item in tree.selection():
                tree.delete(item)
            for i, row in enumerate(tree.get_children(), 1):
                vals = list(tree.item(row)["values"])
                vals[0] = i
                tree.item(row, values=vals)

        ctx.add_command(label="선택 행 삭제", command=_delete)
        tree.bind("<Button-3>", lambda e: ctx.post(e.x_root, e.y_root))

        btn_frm = tk.Frame(win, bg="#1e1e2e")
        btn_frm.pack(fill="x", padx=12, pady=8)

        def _confirm():
            global mapping
            mapping = [
                (tree.item(r)["values"][1], tree.item(r)["values"][2])
                for r in tree.get_children()
            ]
            self._refresh_mapping_label()
            self._log(f"[매핑 확정] {len(mapping)}쌍\n")
            win.destroy()

        tk.Button(btn_frm, text="✔  매핑 확정",
                  command=_confirm,
                  **self._bkw("#a6e3a1", fg="#1e1e2e", bold=True)
                  ).pack(side="right", padx=4)
        tk.Button(btn_frm, text="✖  닫기",
                  command=win.destroy,
                  **self._bkw("#f38ba8", fg="#1e1e2e")
                  ).pack(side="right")

    def _refresh_mapping_label(self):
        n = len(mapping) if mapping else min(len(src_files), len(dst_files))
        self.lbl_mapping.config(text=f"매핑: {n} 쌍")

    # ── 실행 / 중지 ────────────────────────────────────────

    def _start_run(self):
        global stop_flag, filter_enabled, filter_color
        global filter_date_from, filter_date_to, filter_author
        if not mapping:
            messagebox.showwarning("매핑 없음", "먼저 '매핑 편집'에서 확정하세요.")
            return
        if not output_folder:
            messagebox.showwarning("Output 미설정", "Output 폴더를 설정하세요.")
            return

        filter_enabled   = self.var_filter_enabled.get()
        filter_color     = self.var_filter_color.get().strip().lstrip("#").upper()
        filter_date_from = self.ent_date_from.get().strip()
        filter_date_to   = self.ent_date_to.get().strip()
        filter_author    = self.ent_author.get().strip()

        if filter_enabled:
            if fitz is None:
                messagebox.showerror(
                    "PyMuPDF 필요",
                    "마크업 필터 기능에는 PyMuPDF가 필요합니다.\n"
                    "터미널에서 'pip install PyMuPDF' 실행 후 다시 시도하세요."
                )
                return

        stop_flag = False
        self._set_status("실행 중…", "#a6e3a1")
        self.progress["maximum"] = len(mapping)
        self.progress["value"]   = 0
        threading.Thread(target=self._run_worker, daemon=True).start()

    def _stop(self):
        global stop_flag
        stop_flag = True
        self._set_status("중지 요청됨…", "#f9e2af")

    def _run_worker(self):
        for i, (src, dst) in enumerate(mapping, 1):
            if stop_flag:
                self._log("\n⏹ 사용자에 의해 중지됨\n")
                self._set_status("중지됨", "#f38ba8")
                return

            self._log(f"\n[{i}/{len(mapping)}] {src}  →  {dst}\n")
            self._set_status(f"처리 중: {i} / {len(mapping)}")

            out_path = os.path.join(output_folder, dst)

            filter_settings = None
            if filter_enabled:
                filter_settings = dict(
                    color=filter_color,
                    date_from=filter_date_from,
                    date_to=filter_date_to,
                    author=filter_author,
                )

            try:
                process_pair(
                    src=os.path.join(src_folder, src),
                    dst=os.path.join(dst_folder, dst),
                    out=out_path,
                    log_fn=self._log,
                    stop_check=lambda: stop_flag,
                    filter_settings=filter_settings
                )
            except StoppedError:
                close_pdf_discard()
                self._log("\n⏹ 사용자에 의해 중지됨\n")
                self._set_status("중지됨", "#f38ba8")
                return
            except Exception as e:
                self._log(f"  ⚠ 오류: {e}\n")

            self.progress["value"] = i

            # Output 탭 목록 갱신
            out_files = sorted(
                f for f in os.listdir(output_folder) if f.lower().endswith(".pdf")
            )
            self.after(0, lambda fl=out_files: self._update_listbox(self.list_out, fl))

        self._log("\n✅ 모든 작업 완료!\n")
        self._set_status("완료 ✅", "#a6e3a1")

        # 작업 완료 후 Output 폴더 자동 열기
        if output_folder:
            os.startfile(output_folder)

    # ── 로그 헬퍼 ──────────────────────────────────────────

    def _log(self, msg: str):
        def _write():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _write)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_status(self, msg: str, color: str = "#a6adc8"):
        self.after(0, lambda: self.lbl_status.config(text=msg, fg=color))


# ══════════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = App()
    app.mainloop()

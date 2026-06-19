import os
import csv
import math
import re
import shutil
import tempfile
import traceback
import time
import threading
import multiprocessing as mp
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
filter_date_limit = ""  # "YYYY-MM-DD" (이 날짜까지 작성된 마크업만 포함)
filter_author    = ""   # 작성자(사번)

# 위치 보정 설정 (도면 레이아웃이 다른 경우) — Train 번호만 다른 동일 Tag 자동 매칭으로 기준점 계산
pos_correction_enabled = False


def _list_pdfs(path: str) -> list:
    """폴더 내 PDF 목록 (필터 작업용 임시 파일 '_filtered_*'는 제외)"""
    return [
        f for f in os.listdir(path)
        if f.lower().endswith(".pdf") and not f.startswith("_filtered_")
    ]



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
    """저장 없이 닫기 — 저장 여부 팝업이 뜬 경우에만 N으로 닫음
    (팝업이 없을 때 무조건 'n'을 누르면 Bluebeam의 Note 도구 단축키(N)가
    실행되어 다음 작업 중 의도치 않은 Note 마크업이 생성되는 문제가 있었음)"""
    pyautogui.hotkey('ctrl', 'w')
    time.sleep(WAIT_SHORT)
    try:
        active = gw.getActiveWindow()
        title = (active.title if active else "") or ""
    except Exception:
        title = ""
    if title and "Bluebeam" not in title:
        pyautogui.press('n')
        time.sleep(WAIT_SHORT)


def close_bluebeam_app():
    """현재 Bluebeam 창을 완전히 종료한다.
    - 전체화면 상태로 남아 응답 없음(렉)이 발생하는 것을 방지하기 위해 먼저 Escape로 빠져나옴
    - 작업이 끝난 PDF 창이 계속 쌓이지 않도록 Alt+F4로 앱 전체를 닫음
    - 완전히 닫힐 때까지 최대 15초 대기"""
    pyautogui.press('escape')
    time.sleep(WAIT_SHORT)

    wins = gw.getWindowsWithTitle("Bluebeam")
    if not wins:
        return
    try:
        wins[0].activate()
        time.sleep(0.3)
    except Exception:
        pass

    pyautogui.hotkey('alt', 'f4')
    time.sleep(WAIT_SHORT)

    # 저장 확인 팝업 처리 (변경사항 있을 경우)
    try:
        active = gw.getActiveWindow()
        title = (active.title if active else "") or ""
    except Exception:
        title = ""
    if title and "Bluebeam" not in title:
        pyautogui.press('n')
        time.sleep(WAIT_SHORT)

    # Bluebeam이 완전히 닫힐 때까지 최대 15초 대기
    for _ in range(30):
        time.sleep(0.5)
        try:
            remaining = gw.getWindowsWithTitle("Bluebeam")
        except Exception:
            remaining = []
        if not remaining:
            break


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


def _normalize_date_input(date_str):
    """사용자 입력 날짜를 'YYYY-MM-DD'로 정규화 (2026.05.08, 2026/5/8 등 허용, 실패 시 "")"""
    s = (date_str or "").strip()
    if not s:
        return ""
    parts = s.replace(".", "-").replace("/", "-").split("-")
    if len(parts) != 3:
        return ""
    try:
        y, m, d = (int(p) for p in parts)
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return ""


def _get_font_color(annot):
    """FreeText(텍스트) 마크업의 글꼴 색상을 /DA 문자열에서 추출 (RGB 0~1 튜플, 실패 시 None)"""
    try:
        da = annot.parent.xref_get_key(annot.xref, "DA")
    except Exception:
        return None
    if not da or da[0] != "string":
        return None
    parts = da[1].split()
    for i, tok in enumerate(parts):
        if tok == "rg" and i >= 3:
            try:
                return tuple(float(parts[i - 3 + j]) for j in range(3))
            except (ValueError, IndexError):
                return None
        if tok == "g" and i >= 1:
            try:
                v = float(parts[i - 1])
                return (v, v, v)
            except (ValueError, IndexError):
                return None
    return None


def _annot_matches_filter(annot, color_hex, date_limit, author):
    """주어진 조건을 모두 만족해야 True (조건이 비어있으면 해당 항목은 통과)"""
    if color_hex:
        colors = annot.colors or {}
        target = tuple(int(color_hex[i:i + 2], 16) / 255 for i in (0, 2, 4))

        def _close(c):
            return bool(c) and len(c) == 3 and all(
                abs(a - b) <= 0.15 for a, b in zip(c, target)
            )

        # FreeText(텍스트) 마크업: "글꼴 색상" 기준
        # 그 외 마크업: "색" (테두리/기본색, stroke) 기준
        if annot.type[1] == "FreeText":
            font_color = _get_font_color(annot)
            # 콜아웃형 FreeText는 /DA의 글꼴색이 실제 표시 색(테두리/채우기)과
            # 다를 수 있으므로, 글꼴 색·테두리색·채우기색 중 하나라도 맞으면 통과
            if not (
                _close(font_color) or
                _close(colors.get("stroke")) or
                _close(colors.get("fill"))
            ):
                return False
        else:
            if not (_close(colors.get("stroke")) or _close(colors.get("fill"))):
                return False

    if author:
        annot_author = (annot.info.get("title") or "").strip()
        if not annot_author:
            # 일부 마크업 유형은 annot.info에 title이 채워지지 않으므로
            # PDF의 /T 키를 직접 읽어 대체 확인
            try:
                kind, val = annot.parent.xref_get_key(annot.xref, "T")
                if kind == "string":
                    annot_author = val.strip()
            except Exception:
                pass
        if annot_author.lower() != author.strip().lower():
            return False

    if date_limit:
        # 마크업이 "작성된" 날짜(creationDate) 기준, 없으면 수정일(modDate)로 대체
        d = _parse_pdf_date(annot.info.get("creationDate") or annot.info.get("modDate"))
        if d is None:
            return False
        if d > date_limit:
            return False

    return True


def build_filtered_copy(src_path, color_hex, date_limit, author, log_fn=None):
    """필터 조건에 맞지 않는 마크업(annotation)을 제거한 임시 PDF를 만들어
    그 경로를 반환한다. fitz(PyMuPDF)가 없으면 RuntimeError."""
    if fitz is None:
        raise RuntimeError("PyMuPDF(fitz)가 설치되어 있지 않습니다. 'pip install PyMuPDF' 필요")

    doc = fitz.open(src_path)
    kept, removed = 0, 0
    authors_seen = set()
    for page in doc:
        for annot in page.annots() or []:
            a = (annot.info.get("title") or "").strip()
            if not a:
                try:
                    kind, val = annot.parent.xref_get_key(annot.xref, "T")
                    if kind == "string":
                        a = val.strip()
                except Exception:
                    pass
            if a:
                authors_seen.add(a)

        # 삭제할 annot의 xref를 하나씩 찾아 삭제 -> 매번 처음부터 다시 스캔
        # (delete_annot 호출 후에는 기존에 모아둔 annot/xref가 무효화될 수 있음)
        while True:
            target_xref = None
            for annot in page.annots() or []:
                if _annot_matches_filter(annot, color_hex, date_limit, author):
                    continue
                target_xref = annot.xref
                break

            if target_xref is None:
                break

            annot = page.load_annot(target_xref)
            page.delete_annot(annot)
            removed += 1

        for annot in page.annots() or []:
            kept += 1

    if log_fn:
        log_fn(f"  [필터] 유지 {kept}개 / 제외 {removed}개\n")
        if author:
            log_fn(f"  [필터] 발견된 작성자 목록: {sorted(authors_seen)}\n")

    fd, tmp_path = tempfile.mkstemp(
        suffix=".pdf", prefix="_filtered_", dir=tempfile.gettempdir()
    )
    os.close(fd)
    doc.save(tmp_path)
    doc.close()
    return tmp_path, kept, removed


# ══════════════════════════════════════════════════════════
#  위치 보정 (도면 레이아웃이 다른 경우) — PyMuPDF 직접 복사
# ══════════════════════════════════════════════════════════

# Train 번호(앞쪽 2~4자리 숫자)만 다르고 나머지는 동일한 Tag 패턴
# 예: "504-CWS-0100-400-ACB3B02SE51-NN" / "604-CWS-0100-400-ACB3B02SE51-NN"
_TAG_RE = re.compile(r'^(\d{2,4})-([A-Za-z0-9][A-Za-z0-9-]{4,})$')


def _extract_tag_suffixes(page):
    """페이지에서 'NNN-나머지' 형태의 Tag 텍스트를 추출해 suffix(나머지 부분)별
    위치(중심점)를 반환. 같은 suffix가 페이지에 여러 번 나오면 어느 게 맞는지
    모호하므로 매칭에서 제외한다."""
    suffix_map = {}
    try:
        words = page.get_text("words")
    except Exception:
        return {}
    for w in words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        m = _TAG_RE.match(text)
        if not m:
            continue
        suffix = m.group(2)
        center = ((x0 + x1) / 2, (y0 + y1) / 2)
        if suffix in suffix_map:
            suffix_map[suffix] = None  # 중복 발견 → 모호함 표시
        else:
            suffix_map[suffix] = center
    return {k: v for k, v in suffix_map.items() if v is not None}


def _find_tag_matches(src_doc, dst_doc, log_fn=None):
    """Source/Target 문서에서 Train 번호만 다른 동일 Tag(suffix)를 자동으로
    찾아 매칭한다. 수동 입력 불필요.
    반환: (src_page_idx, dst_page_idx, [(src_pt, dst_pt), ...]) — 못 찾으면 None"""
    def log(msg):
        if log_fn:
            log_fn(msg)

    for sp in src_doc:
        src_map = _extract_tag_suffixes(sp)
        if not src_map:
            continue
        for dp in dst_doc:
            dst_map = _extract_tag_suffixes(dp)
            common = sorted(set(src_map) & set(dst_map))
            if len(common) >= 2:
                pairs = [(src_map[s], dst_map[s]) for s in common]
                log(f"  [위치 보정] 자동 Tag 매칭 {len(pairs)}개 발견 "
                    f"(Source p{sp.number + 1} → Target p{dp.number + 1})\n")
                for s in common[:10]:
                    log(f"    - {s}\n")
                return sp.number, dp.number, pairs
    return None


def _fit_similarity_matrix(pairs):
    """여러 개의 (src_pt, dst_pt) 매칭 쌍으로 이동+회전+스케일을 포함한
    2D 유사변환 행렬(fitz.Matrix)을 최소제곱으로 계산한다.
    매칭 쌍이 4개 이상이면 잔차가 큰 이상치를 1회 걸러내고 재계산한다."""
    if len(pairs) < 2:
        raise ValueError("매칭된 Tag가 2개 미만이라 위치 보정을 계산할 수 없습니다.")

    def fit(pts):
        s = [complex(*p[0]) for p in pts]
        d = [complex(*p[1]) for p in pts]
        n = len(s)
        s_mean = sum(s) / n
        d_mean = sum(d) / n
        den = sum(abs(si - s_mean) ** 2 for si in s)
        if den < 25:  # 기준점들이 거의 한 점에 몰려 있음 (분산 < 5pt^2 수준)
            raise ValueError("매칭된 Tag들의 위치가 서로 너무 가깝습니다.")
        num = sum((si - s_mean).conjugate() * (di - d_mean) for si, di in zip(s, d))
        k = num / den
        t = d_mean - k * s_mean
        return k, t

    k, t = fit(pairs)

    if len(pairs) >= 4:
        residuals = [abs(k * complex(*sp) + t - complex(*dp)) for sp, dp in pairs]
        med = sorted(residuals)[len(residuals) // 2]
        threshold = max(med * 4, 10)
        filtered = [p for p, r in zip(pairs, residuals) if r <= threshold]
        if 2 <= len(filtered) < len(pairs):
            k, t = fit(filtered)

    a, b = k.real, k.imag
    e, f = t.real, t.imag
    if not all(math.isfinite(v) for v in (a, b, e, f)):
        raise ValueError("변환 행렬 계산에 실패했습니다(비정상 좌표).")

    scale = abs(k)
    if scale < 0.1 or scale > 10:
        raise ValueError(
            f"계산된 스케일({scale:.2f}배)이 비정상적입니다. "
            "Tag 매칭이 잘못되었을 가능성이 높습니다."
        )
    return fitz.Matrix(a, b, -b, a, e, f)


def _is_finite_point(pt) -> bool:
    return math.isfinite(pt[0]) and math.isfinite(pt[1]) and \
        abs(pt[0]) < 1_000_000 and abs(pt[1]) < 1_000_000


def _is_finite_rect(rect) -> bool:
    return all(math.isfinite(v) for v in (rect.x0, rect.y0, rect.x1, rect.y1)) and \
        abs(rect.x0) < 1_000_000 and abs(rect.y0) < 1_000_000 and \
        abs(rect.x1) < 1_000_000 and abs(rect.y1) < 1_000_000 and \
        rect.width > 0.01 and rect.height > 0.01


# PyMuPDF로 직접 재생성 가능한 마크업 타입(geometry 기반)
_SUPPORTED_TRANSFORM_TYPES = {
    "Square", "Circle", "Line", "PolyLine", "Polygon",
    "FreeText", "Highlight", "Ink", "StrikeOut", "Underline", "Squiggly",
}


def _copy_annot_with_transform(src_annot, dst_page, matrix):
    """src_annot의 geometry를 matrix로 변환해 dst_page에 동일한 종류의 마크업을 생성.
    지원하지 않는 타입이면 False 반환(스킵)."""
    subtype = src_annot.type[1]
    colors = src_annot.colors or {}
    stroke = colors.get("stroke")
    fill = colors.get("fill")
    border_info = src_annot.border or {}
    width = border_info.get("width", 1) or 1
    clouds = border_info.get("clouds", 0) or 0
    dashes = border_info.get("dashes") or None
    opacity = src_annot.opacity if src_annot.opacity is not None else 1

    def tp(pt):
        """튜플/Point를 fitz.Point로 변환 후 matrix 적용"""
        return fitz.Point(pt[0], pt[1]) * matrix

    new_annot = None

    try:
        if subtype in ("Square",):
            rect = src_annot.rect * matrix
            if not _is_finite_rect(rect):
                return False
            new_annot = dst_page.add_rect_annot(rect)
        elif subtype == "Circle":
            rect = src_annot.rect * matrix
            if not _is_finite_rect(rect):
                return False
            new_annot = dst_page.add_circle_annot(rect)
        elif subtype in ("Line",):
            verts = src_annot.vertices
            pts = [tp(p) for p in verts] if verts else None
            if not pts:
                v = src_annot.line
                pts = [tp(v[0]), tp(v[1])] if v else None
            if not pts or len(pts) < 2 or not all(_is_finite_point(p) for p in pts):
                return False
            new_annot = dst_page.add_line_annot(pts[0], pts[1])
        elif subtype in ("PolyLine", "Polygon"):
            verts = src_annot.vertices
            if not verts:
                return False
            pts = [tp(v) for v in verts]
            if not all(_is_finite_point(p) for p in pts):
                return False
            if subtype == "PolyLine":
                new_annot = dst_page.add_polyline_annot(pts)
            else:
                new_annot = dst_page.add_polygon_annot(pts)
        elif subtype == "Ink":
            try:
                strokes = src_annot.ink_list
            except Exception:
                return False
            if not strokes:
                return False
            transformed = [[tp(pt) for pt in stroke] for stroke in strokes]
            if not all(_is_finite_point(p) for stroke in transformed for p in stroke):
                return False
            new_annot = dst_page.add_ink_annot(transformed)
        elif subtype == "FreeText":
            rect = src_annot.rect * matrix
            if not _is_finite_rect(rect):
                return False
            text = (src_annot.info or {}).get("content", "") or ""
            new_annot = dst_page.add_freetext_annot(rect, text)
        elif subtype in ("Highlight", "StrikeOut", "Underline", "Squiggly"):
            quads = src_annot.vertices
            if not quads or len(quads) < 4:
                return False
            # vertices: 4개씩 끊어서 quad 단위로 변환
            flat = [tp(pt) for pt in quads]
            if not all(_is_finite_point(p) for p in flat):
                return False
            if subtype == "Highlight":
                new_annot = dst_page.add_highlight_annot(flat)
            elif subtype == "StrikeOut":
                new_annot = dst_page.add_strikeout_annot(flat)
            elif subtype == "Underline":
                new_annot = dst_page.add_underline_annot(flat)
            else:
                new_annot = dst_page.add_squiggly_annot(flat)
        else:
            return False
    except Exception:
        return False

    if new_annot is None:
        return False

    try:
        col_kwargs = {}
        if stroke:
            col_kwargs["stroke"] = stroke
        if fill:
            col_kwargs["fill"] = fill
        if col_kwargs:
            new_annot.set_colors(**col_kwargs)

        scale = math.hypot(matrix.a, matrix.b)
        border_kwargs = {"width": width * scale}
        if dashes:
            border_kwargs["dashes"] = dashes
        if clouds and subtype in ("Square", "Circle", "Polygon", "PolyLine"):
            border_kwargs["clouds"] = clouds
        try:
            new_annot.set_border(**border_kwargs)
        except Exception:
            new_annot.set_border(width=width * scale)

        new_annot.set_opacity(opacity)
        new_annot.update()
    except Exception:
        pass

    return True


def copy_markups_with_position_correction(src_path, dst_path, out_path, log_fn=None):
    """도면 레이아웃이 다른 경우: Bluebeam 자동화 없이 PyMuPDF로 직접
    마크업 좌표를 보정해서 Target(→Output)에 복사한다.
    기준점은 Train 번호만 다르고 나머지는 동일한 Tag(배관선번호 등)를
    Source/Target에서 자동으로 찾아 매칭해서 사용 — 수동 입력 불필요.
    반환: (copied, skipped) 마크업 개수"""
    def log(msg):
        if log_fn:
            log_fn(msg)

    if fitz is None:
        raise RuntimeError("PyMuPDF(fitz)가 설치되어 있지 않습니다. 'pip install PyMuPDF' 필요")

    src_doc = fitz.open(src_path)
    dst_doc = fitz.open(dst_path)

    match_result = _find_tag_matches(src_doc, dst_doc, log_fn=log)
    if not match_result:
        src_doc.close()
        dst_doc.close()
        raise RuntimeError(
            "위치 보정 실패: Source/Target에서 Train 번호만 다른 동일 Tag를 "
            "2개 이상 찾지 못했습니다."
        )

    src_page_idx, dst_page_idx, pairs = match_result
    matrix = _fit_similarity_matrix(pairs)
    log(f"  [위치 보정] Tag {len(pairs)}개 기준 변환 행렬 계산 완료 "
        f"(Source p{src_page_idx+1} → Target p{dst_page_idx+1})\n")

    src_page = src_doc[src_page_idx]
    dst_page = dst_doc[dst_page_idx]

    copied, skipped = 0, 0
    for annot in src_page.annots() or []:
        ok = _copy_annot_with_transform(annot, dst_page, matrix)
        if ok:
            copied += 1
        else:
            skipped += 1

    if skipped:
        log(f"  [위치 보정] ⚠ {skipped}개 마크업은 지원하지 않는 타입이라 스킵됨\n")
    log(f"  [위치 보정] {copied}개 마크업 좌표 보정하여 복사 완료\n")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    dst_doc.save(out_path)
    src_doc.close()
    dst_doc.close()
    return copied, skipped


def _position_correction_subprocess_main(src_path, dst_path, out_path, result_queue):
    """별도 프로세스에서 실행되는 진입점. PyMuPDF 네이티브 크래시가 나도
    이 프로세스만 죽고 메인 GUI 프로세스는 영향받지 않는다."""
    try:
        copied, skipped = copy_markups_with_position_correction(
            src_path, dst_path, out_path, log_fn=None
        )
        result_queue.put(("ok", copied, skipped))
    except Exception as e:
        result_queue.put(("error", str(e)))


def run_position_correction_isolated(src_path, dst_path, out_path,
                                     log_fn=None, timeout=90):
    """위치 보정을 별도 프로세스에서 실행해 네이티브 크래시로부터 메인 앱을 보호한다."""
    def log(msg):
        if log_fn:
            log_fn(msg)

    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue()
    proc = ctx.Process(
        target=_position_correction_subprocess_main,
        args=(src_path, dst_path, out_path, result_queue),
    )
    proc.start()
    proc.join(timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join()
        raise RuntimeError(f"위치 보정 작업이 {timeout}초 내에 끝나지 않아 중단했습니다.")

    try:
        result = result_queue.get_nowait()
    except Exception:
        exitcode = proc.exitcode
        raise RuntimeError(
            f"위치 보정 작업이 비정상 종료되었습니다 (exit code {exitcode}). "
            "PyMuPDF 내부 오류로 추정됩니다. Source/Target에 매칭 가능한 Tag가 있는지 확인하세요."
        )

    status = result[0]
    if status == "error":
        raise RuntimeError(result[1])
    return result[1], result[2]   # copied, skipped


def _open_and_copy_source(src: str, filter_settings=None, log_fn=None):
    """Source PDF를 열고 마크업을 클립보드에 복사한 뒤 닫는다.
    반환: (filter_kept, filter_removed, tmp_path_or_None)"""
    def log(msg):
        if log_fn:
            log_fn(msg)

    open_src = src
    filter_kept, filter_removed = None, None
    tmp_to_delete = None

    if filter_settings:
        log("  [필터] 마크업 필터 적용 중…\n")
        open_src, filter_kept, filter_removed = build_filtered_copy(
            src,
            filter_settings.get("color", ""),
            filter_settings.get("date_limit", ""),
            filter_settings.get("author", ""),
            log_fn=log,
        )
        if open_src != src:
            tmp_to_delete = open_src

    log(f"  [Source] 열기: {os.path.basename(src)}\n")
    open_pdf(open_src)
    fit_page()
    time.sleep(WAIT_SHORT)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(WAIT_SHORT)

    if filter_settings:
        log("  [필터] 병합 마크업 그룹 해제 중…\n")
        pyautogui.hotkey('ctrl', 'shift', 'g')
        time.sleep(WAIT_SHORT)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(WAIT_SHORT)

    pyautogui.hotkey('ctrl', 'c')
    time.sleep(WAIT_SHORT)

    log("  [Source] 마크업 복사 완료 → Source 닫기\n")
    close_pdf_discard()

    if tmp_to_delete:
        try:
            os.remove(tmp_to_delete)
        except OSError:
            pass

    return filter_kept, filter_removed


def _paste_to_target(dst: str, out: str, log_fn=None, stop_check=None):
    """클립보드 내용을 Target → Output에 붙여넣고 저장 후 해당 탭만 닫는다."""
    def log(msg):
        if log_fn:
            log_fn(msg)

    def check_stop():
        if stop_check and stop_check():
            raise StoppedError()

    check_stop()
    log(f"  [Target] Output 복사: {os.path.basename(out)}\n")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copy2(dst, out)
    time.sleep(0.5)

    log(f"  [Target] 열기 → 마크업 붙여넣기 → 저장\n")
    open_pdf(out)
    fit_page()
    time.sleep(WAIT_SHORT)
    check_stop()

    pyautogui.hotkey('ctrl', 'shift', 'v')   # Paste in Place
    time.sleep(WAIT_PASTE)

    pyautogui.hotkey('ctrl', 's')            # Save
    time.sleep(2.5)
    pyautogui.press('enter')                 # 덮어쓰기 확인 팝업 대비
    time.sleep(1.0)

    # 탭만 닫기 (Bluebeam 앱은 유지) — 저장 완료 후라 저장 팝업 없음
    pyautogui.hotkey('ctrl', 'w')
    time.sleep(WAIT_SHORT)
    log("  ✓ 완료\n")


def process_pair(src: str, dst: str, out: str, log_fn=None, stop_check=None,
                 filter_settings=None):
    """단일 Source → 단일 Target 처리 (하위 호환용)"""
    def check_stop():
        if stop_check and stop_check():
            raise StoppedError()

    check_stop()
    filter_kept, filter_removed = _open_and_copy_source(src, filter_settings, log_fn)
    check_stop()
    _paste_to_target(dst, out, log_fn, stop_check)
    return {"filter_kept": filter_kept, "filter_removed": filter_removed}


def process_source_group(src: str, targets: list, log_fn=None, stop_check=None,
                         filter_settings=None):
    """Source 하나 → Target 여러 개 처리 (Source를 한 번만 열고 복사).
    targets: [(dst_path, out_path), ...]
    반환: [(filter_kept, filter_removed), ...] — 첫 Target 기준 필터 결과, 나머지는 동일"""
    def check_stop():
        if stop_check and stop_check():
            raise StoppedError()

    check_stop()
    filter_kept, filter_removed = _open_and_copy_source(src, filter_settings, log_fn)
    check_stop()

    results = []
    for dst, out in targets:
        _paste_to_target(dst, out, log_fn, stop_check)
        results.append((filter_kept, filter_removed))
        check_stop()
    return results


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
                                        self._select_source, "#a6e3a1",
                                        folder_cmd=self._select_source_folder)
        self.lbl_dst = self._folder_row(folder_frm, 1, "Target 폴더",
                                        self._select_target, "#89b4fa",
                                        folder_cmd=self._select_target_folder)
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
        btn_frm.columnconfigure(0, weight=1)
        btn_frm.columnconfigure(1, weight=1)
        btn_frm.columnconfigure(2, weight=1)
        btn_frm.columnconfigure(3, weight=1)

        tk.Button(btn_frm, text="매핑 편집",
                  command=self._open_mapping,
                  **self._bkw("#7f849c")
                  ).grid(row=0, column=0, sticky="ew", padx=2)
        tk.Button(btn_frm, text="🗑 초기화",
                  command=self._reset_mapping,
                  **self._bkw("#585b70")
                  ).grid(row=1, column=0, sticky="ew", padx=2, pady=(2, 0))

        tk.Button(btn_frm, text="📂 Excel 매핑",
                  command=self._load_mapping_excel,
                  **self._bkw("#cba6f7", fg="#1e1e2e")
                  ).grid(row=0, column=1, sticky="ew", padx=2)

        tk.Button(btn_frm, text="🚀 실행",
                  command=self._start_run,
                  **self._bkw("#a6e3a1", fg="#1e1e2e", bold=True)
                  ).grid(row=0, column=2, sticky="ew", padx=2)

        tk.Button(btn_frm, text="⏹ 중지",
                  command=self._stop,
                  **self._bkw("#f38ba8", fg="#1e1e2e")
                  ).grid(row=0, column=3, sticky="ew", padx=2)

        self.lbl_mapping = tk.Label(
            left, text="매핑: 0 쌍",
            font=("Segoe UI", 9), fg="#a6adc8", bg="#1e1e2e"
        )
        self.lbl_mapping.grid(row=6, column=0, sticky="w")

        # 마크업 필터 섹션
        self._sec(left, "🎯 마크업 필터 (선택)", row=7)
        self._build_filter_section(left, row=8)

        # 위치 보정 섹션 (도면 레이아웃이 다른 경우)
        self._sec(left, "📐 도면 위치 보정 (선택)", row=9)
        self._build_position_section(left, row=10)

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

        self.ent_date_limit = _entry_row(1, "기준 날짜")
        self.ent_author     = _entry_row(2, "작성자(사번)")

        tk.Label(
            frm,
            text="※ 기준 날짜: YYYY-MM-DD (예: 2026-05-08)\n"
                 "   이 날짜까지 작성된 마크업만 Target에 붙여넣습니다.\n"
                 "   작성자: 마크업 작성자(사번)와 정확히 일치해야 함\n"
                 "   (비워두면 해당 조건은 무시)",
            font=("Segoe UI", 8), fg="#6c7086", bg="#1e1e2e",
            justify="left", anchor="w"
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 2))

    def _build_position_section(self, parent, row: int):
        frm = tk.Frame(parent, bg="#1e1e2e")
        frm.grid(row=row, column=0, sticky="ew", pady=(2, 0))
        frm.columnconfigure(1, weight=1)

        self.var_pos_correction = tk.BooleanVar(value=False)
        tk.Checkbutton(
            frm, text="위치 보정 사용 (Source/Target 도면 레이아웃이 다른 경우)",
            variable=self.var_pos_correction,
            font=("Segoe UI", 9), fg="#a6adc8", bg="#1e1e2e",
            selectcolor="#313244", activebackground="#1e1e2e",
            activeforeground="#cdd6f4", wraplength=280, justify="left"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(2, 4))

        tk.Label(
            frm,
            text="※ Train 번호만 다르고 나머지는 동일한 Tag(배관선번호 등)를\n"
                 "   Source/Target에서 자동으로 찾아 기준점으로 사용합니다.\n"
                 "   별도 입력 불필요. (이동 + 회전 + 스케일까지 보정,\n"
                 "   Bluebeam 자동화 없이 직접 적용)",
            font=("Segoe UI", 8), fg="#6c7086", bg="#1e1e2e",
            justify="left", anchor="w"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 2))

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
                    cmd, accent: str, folder_cmd=None) -> tk.Label:
        frm = tk.Frame(parent, bg="#1e1e2e")
        frm.grid(row=row, column=0, sticky="ew", pady=2)
        frm.columnconfigure(2, weight=1)

        tk.Button(frm, text=label, command=cmd, width=13,
                  **self._bkw(accent, fg="#1e1e2e")
                  ).grid(row=0, column=0, sticky="w")

        if folder_cmd is not None:
            tk.Button(frm, text="폴더 전체", command=folder_cmd, width=8,
                      **self._bkw("#6c7086", fg="#cdd6f4")
                      ).grid(row=0, column=1, sticky="w", padx=(4, 0))

        lbl = tk.Label(frm, text="(미설정)",
                       font=("Segoe UI", 8), fg="#6c7086",
                       bg="#1e1e2e", anchor="w", wraplength=220)
        lbl.grid(row=0, column=2, sticky="ew", padx=6)
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
        files = filedialog.askopenfilenames(
            title="Source 폴더 - PDF 파일 선택 (Ctrl+A로 전체 선택)",
            filetypes=[("PDF 파일", "*.pdf")]
        )
        if not files:
            return
        src_folder = os.path.dirname(files[0])
        src_files = sorted(os.path.basename(f) for f in files)
        self.lbl_src.config(
            text=f"{os.path.basename(src_folder)}  ({len(src_files)}개)", fg="#a6adc8"
        )
        self._update_listbox(self.list_src, src_files)
        self.notebook.select(0)
        self._log(f"[Source] {src_folder}  ({len(src_files)}개)\n")
        for f in src_files:
            self._log(f"    - {f}\n")
        self._refresh_mapping_label()

    def _select_source_folder(self):
        global src_folder, src_files
        path = filedialog.askdirectory(title="Source 폴더 선택 (폴더 내 모든 PDF 사용)")
        if not path:
            return
        src_folder = path
        src_files = sorted(_list_pdfs(path))
        self.lbl_src.config(
            text=f"{os.path.basename(src_folder)}  ({len(src_files)}개)", fg="#a6adc8"
        )
        self._update_listbox(self.list_src, src_files)
        self.notebook.select(0)
        self._log(f"[Source] {src_folder}  ({len(src_files)}개)\n")
        for f in src_files:
            self._log(f"    - {f}\n")
        self._refresh_mapping_label()

    def _select_target_folder(self):
        global dst_folder, dst_files
        path = filedialog.askdirectory(title="Target 폴더 선택 (폴더 내 모든 PDF 사용)")
        if not path:
            return
        dst_folder = path
        dst_files = sorted(_list_pdfs(path))
        self.lbl_dst.config(
            text=f"{os.path.basename(dst_folder)}  ({len(dst_files)}개)", fg="#a6adc8"
        )
        self._update_listbox(self.list_dst, dst_files)
        self.notebook.select(1)
        self._log(f"[Target] {dst_folder}  ({len(dst_files)}개)\n")
        for f in dst_files:
            self._log(f"    - {f}\n")
        self._refresh_mapping_label()

    def _select_target(self):
        global dst_folder, dst_files
        files = filedialog.askopenfilenames(
            title="Target 폴더 - PDF 파일 선택 (Ctrl+A로 전체 선택)",
            filetypes=[("PDF 파일", "*.pdf")]
        )
        if not files:
            return
        dst_folder = os.path.dirname(files[0])
        dst_files = sorted(os.path.basename(f) for f in files)
        self.lbl_dst.config(
            text=f"{os.path.basename(dst_folder)}  ({len(dst_files)}개)", fg="#a6adc8"
        )
        self._update_listbox(self.list_dst, dst_files)
        self.notebook.select(1)
        self._log(f"[Target] {dst_folder}  ({len(dst_files)}개)\n")
        for f in dst_files:
            self._log(f"    - {f}\n")
        self._refresh_mapping_label()

    def _select_output(self):
        global output_folder
        path = filedialog.askdirectory(title="Output 폴더 선택")
        if not path:
            return
        output_folder = path
        # Output 폴더의 기존 PDF 표시
        out_files = sorted(_list_pdfs(path))
        self.lbl_out.config(text=os.path.basename(path), fg="#a6adc8")
        self._update_listbox(self.list_out, out_files)
        self.notebook.select(2)
        self._log(f"[Output] {path}  ({len(out_files)}개)\n")
        for f in out_files:
            self._log(f"    - {f}\n")

    def _update_listbox(self, lb: tk.Listbox, files: list):
        lb.delete(0, "end")
        for i, f in enumerate(files, 1):
            lb.insert("end", f"  {i:>3}.  {f}")
        # 짝수 행 색상 구분
        for i in range(0, len(files), 2):
            lb.itemconfig(i, bg="#1e1e2e")
        for i in range(1, len(files), 2):
            lb.itemconfig(i, bg="#181825")

    # ── Excel 매핑 가져오기 ─────────────────────────────────

    def _load_mapping_excel(self):
        """Excel 파일로 매핑 가져오기.

        Excel 형식:
          1행: 헤더 (무시)
          2행: 폴더 경로행 — A2=Source 폴더, B2~=각 Train의 Target 폴더
               (셀 값에 경로 구분자 \\ 또는 / 포함 시 경로행으로 자동 인식)
          3행~: 파일명행 — A열=Source 파일명, B~N열=Train별 Target 파일명
               (Source 하나에 Target 여러 개 → 각각 별도 매핑 쌍으로 확장)
        """
        global mapping, src_files, dst_files, src_folder, dst_folder
        try:
            import openpyxl
        except ImportError:
            messagebox.showerror(
                "openpyxl 필요",
                "Excel 매핑 기능에는 openpyxl이 필요합니다.\n"
                "터미널에서 'pip install openpyxl' 실행 후 다시 시도하세요."
            )
            return

        path = filedialog.askopenfilename(
            title="매핑 Excel 파일 선택",
            filetypes=[("Excel 파일", "*.xlsx *.xls"), ("모든 파일", "*.*")]
        )
        if not path:
            return

        try:
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
        except Exception as e:
            messagebox.showerror("파일 오류", f"Excel 파일을 열 수 없습니다:\n{e}")
            return

        def _is_path(val):
            s = str(val or "").strip()
            return bool(s) and ("\\" in s or "/" in s or (len(s) > 2 and s[1] == ":"))

        def _as_str(val):
            s = str(val or "").strip()
            if not s or s.lower() == "none":
                return ""
            return s

        def _ensure_pdf(name):
            return name if name.lower().endswith(".pdf") else name + ".pdf"

        # 모든 행 읽기 (헤더 1행 스킵)
        all_rows = list(ws.iter_rows(min_row=2, values_only=True))
        if not all_rows:
            messagebox.showwarning("내용 없음", "Excel 파일에 데이터가 없습니다.")
            return

        # 폴더 경로 행 감지: 첫 데이터행(2행)에 경로 구분자가 있으면 폴더 행으로 간주
        col_dirs = []   # 열별 Target 폴더 경로 (col 0 = Source 폴더)
        data_start = 0
        first = all_rows[0]
        if any(_is_path(c) for c in first if c):
            col_dirs = [_as_str(c) for c in first]
            data_start = 1
            # Source 폴더(A열 경로)가 있으면 GUI lbl_src 갱신
            if col_dirs[0]:
                self._log(f"[Excel 매핑] Source 폴더: {col_dirs[0]}\n")

        n_targets = (ws.max_column - 1)  # A열 제외 나머지 = Target 열 수

        new_mapping = []
        for row in all_rows[data_start:]:
            src_name = _as_str(row[0])
            if not src_name:
                continue
            src_name = _ensure_pdf(src_name)
            s_dir = col_dirs[0] if col_dirs else src_folder

            for col_i in range(1, len(row)):
                dst_name = _as_str(row[col_i])
                if not dst_name:
                    continue
                dst_name = _ensure_pdf(dst_name)
                d_dir = col_dirs[col_i] if col_dirs and col_i < len(col_dirs) else dst_folder
                new_mapping.append((src_name, dst_name, s_dir, d_dir))

        if not new_mapping:
            messagebox.showwarning("매핑 없음",
                "Excel에서 유효한 매핑을 찾지 못했습니다.\n\n"
                "형식:\n"
                "  1행: 헤더 (무시)\n"
                "  2행: 폴더 경로 (선택, 경로 구분자 포함 시 자동 인식)\n"
                "  3행~: A열=Source 파일명, B~열=Train별 Target 파일명")
            return

        mapping   = new_mapping
        src_files = list(dict.fromkeys(r[0] for r in mapping))
        dst_files = list(dict.fromkeys(r[1] for r in mapping))

        self._update_listbox(self.list_src, src_files)
        self._update_listbox(self.list_dst, dst_files)
        self._refresh_mapping_label()

        # 폴더 라벨 업데이트
        if col_dirs and col_dirs[0]:
            src_folder = col_dirs[0]
            self.lbl_src.config(text=os.path.basename(src_folder.rstrip("/\\")) or src_folder,
                                fg="#a6e3a1")
        dst_dirs = [d for _, _, _, d in mapping if d]
        if dst_dirs:
            dst_folder = dst_dirs[0]
            unique_dst_dirs = list(dict.fromkeys(d for _, _, _, d in mapping if d))
            label = unique_dst_dirs[0] if len(unique_dst_dirs) == 1 else f"{len(unique_dst_dirs)}개 폴더"
            self.lbl_dst.config(text=os.path.basename(label.rstrip("/\\")) or label,
                                fg="#a6e3a1")

        self._log(f"[Excel 매핑] {len(mapping)}개 매핑 로드 ← {os.path.basename(path)}\n")
        for s, d, sd, dd in mapping:
            self._log(f"    [{os.path.basename(sd or src_folder)}] {s}  →  [{os.path.basename(dd or dst_folder)}] {d}\n")

        messagebox.showinfo("Excel 매핑 완료",
                            f"{len(mapping)}개 매핑을 가져왔습니다.\n"
                            f"'매핑 편집'에서 확인/수정 가능합니다.")

    # ── 매핑 편집 창 ───────────────────────────────────────

    def _reset_mapping(self):
        global mapping, src_files, dst_files
        if not mapping and not src_files and not dst_files:
            return
        if messagebox.askyesno("매핑 초기화", "현재 매핑을 모두 지울까요?"):
            mapping = []
            src_files.clear()
            dst_files.clear()
            self.list_src.delete(0, tk.END)
            self.list_dst.delete(0, tk.END)
            self.lbl_mapping.config(text="매핑: 0 쌍")
            self._log("[매핑 초기화]\n")

    def _open_mapping(self):
        global mapping
        if not src_files or not dst_files:
            if not mapping:
                messagebox.showwarning("폴더 미설정", "Source / Target 폴더를 먼저 선택하거나 Excel 매핑을 불러오세요.")
                return

        if not mapping:
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
            mapping = [(s, d, src_folder, dst_folder) for s, d in zip(use_src, use_dst)]

        win = tk.Toplevel(self)
        win.title(f"파일 매핑 편집  ({len(mapping)}쌍)")
        win.geometry("1050x520")
        win.configure(bg="#1e1e2e")
        win.grab_set()

        tk.Label(win, text="우클릭 → 선택 행 삭제  |  확정 버튼을 눌러야 반영됩니다",
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
                            columns=("#", "src", "src_dir", "dst", "dst_dir"),
                            show="headings")
        tree.heading("#",       text="#",           anchor="center")
        tree.heading("src",     text="Source 파일명")
        tree.heading("src_dir", text="Source 폴더")
        tree.heading("dst",     text="Target 파일명")
        tree.heading("dst_dir", text="Target 폴더")
        tree.column("#",       width=36,  anchor="center", stretch=False)
        tree.column("src",     width=260)
        tree.column("src_dir", width=180)
        tree.column("dst",     width=260)
        tree.column("dst_dir", width=180)

        vsb = ttk.Scrollbar(frm, orient="vertical",   command=tree.yview)
        hsb = ttk.Scrollbar(frm, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        for i, row in enumerate(mapping, 1):
            s  = row[0]; d  = row[1]
            sd = row[2] if len(row) > 2 else ""
            dd = row[3] if len(row) > 3 else ""
            tree.insert("", "end", values=(i, s, sd, d, dd))

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
                (tree.item(r)["values"][1],
                 tree.item(r)["values"][3],
                 tree.item(r)["values"][2],
                 tree.item(r)["values"][4])
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
        n = len(mapping)
        self.lbl_mapping.config(text=f"매핑: {n} 쌍")

    # ── 실행 / 중지 ────────────────────────────────────────

    def _start_run(self):
        global stop_flag, filter_enabled, filter_color
        global filter_date_limit, filter_author
        global pos_correction_enabled
        if not mapping:
            messagebox.showwarning("매핑 없음", "먼저 '매핑 편집'에서 확정하세요.")
            return
        if not output_folder:
            messagebox.showwarning("Output 미설정", "Output 폴더를 설정하세요.")
            return

        filter_enabled   = self.var_filter_enabled.get()
        filter_color     = ""
        filter_date_limit = _normalize_date_input(self.ent_date_limit.get())
        filter_author    = self.ent_author.get().strip()

        pos_correction_enabled = self.var_pos_correction.get()

        if filter_enabled or pos_correction_enabled:
            if fitz is None:
                messagebox.showerror(
                    "PyMuPDF 필요",
                    "마크업 필터 / 위치 보정 기능에는 PyMuPDF가 필요합니다.\n"
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
        report_rows = []
        try:
            self._run_worker_inner(report_rows)
        except Exception:
            err = traceback.format_exc()
            self._log(f"\n💥 예기치 않은 오류로 작업 중단:\n{err}\n")
            self._set_status("오류로 중단 ❌", "#f38ba8")
            self._write_report(report_rows)
            try:
                close_bluebeam_app()
            except Exception:
                pass

    def _run_worker_inner(self, report_rows):
        filter_settings = None
        if filter_enabled:
            filter_settings = dict(
                color=filter_color,
                date_limit=filter_date_limit,
                author=filter_author,
            )

        # Source별로 그룹핑: {(src_path): [(dst_path, out_path, src_name, dst_name), ...]}
        from collections import OrderedDict
        groups = OrderedDict()
        for row in mapping:
            src, dst = row[0], row[1]
            s_dir = row[2] if len(row) > 2 and row[2] else src_folder
            d_dir = row[3] if len(row) > 3 and row[3] else dst_folder
            src_path = os.path.join(s_dir, src)
            dst_path = os.path.join(d_dir, dst)
            out_path = os.path.join(output_folder, dst)
            groups.setdefault(src_path, []).append((dst_path, out_path, src, dst))

        total = len(mapping)
        done = 0

        for src_path, targets in groups.items():
            src_name = os.path.basename(src_path)
            n = len(targets)
            self._log(f"\n▶ Source: {src_name}  ({n}개 Target)\n")
            self._set_status(f"처리 중: {done + 1}~{done + n} / {total}")

            if stop_flag:
                self._log("\n⏹ 사용자에 의해 중지됨\n")
                self._set_status("중지됨", "#f38ba8")
                self._write_report(report_rows)
                return

            if pos_correction_enabled:
                done = self._process_group_with_position_correction(
                    src_path, targets, filter_settings, report_rows, done, total
                )
                continue

            # Source 열기 → 복사 (한 번만)
            start_src = time.time()
            try:
                filter_kept, filter_removed = _open_and_copy_source(
                    src_path, filter_settings, self._log
                )
            except StoppedError:
                self._log("\n⏹ 사용자에 의해 중지됨\n")
                self._set_status("중지됨", "#f38ba8")
                self._write_report(report_rows)
                return
            except Exception:
                err = traceback.format_exc()
                self._log(f"  ⚠ Source 열기 오류:\n{err}\n")
                for _, _, src_name2, dst_name in targets:
                    report_rows.append({
                        "source": src_name2, "target": dst_name, "output": dst_name,
                        "status": "오류(Source)", "filter_kept": "", "filter_removed": "",
                        "elapsed_sec": round(time.time() - start_src, 1),
                        "error": str(err).splitlines()[-1] if err else "",
                    })
                done += n
                self.after(0, lambda v=done: self.progress.configure(value=v))
                continue

            # 각 Target에 붙여넣기
            for dst_path, out_path, src_name2, dst_name in targets:
                done += 1
                self._log(f"  [{done}/{total}] → {dst_name}\n")
                start_time = time.time()
                try:
                    _paste_to_target(dst_path, out_path, self._log, lambda: stop_flag)
                    elapsed = time.time() - start_time
                    report_rows.append({
                        "source": src_name2, "target": dst_name, "output": dst_name,
                        "status": "완료",
                        "filter_kept": filter_kept,
                        "filter_removed": filter_removed,
                        "elapsed_sec": round(elapsed, 1),
                        "error": "",
                    })
                except StoppedError:
                    self._log("\n⏹ 사용자에 의해 중지됨\n")
                    self._set_status("중지됨", "#f38ba8")
                    self._write_report(report_rows)
                    return
                except Exception:
                    err = traceback.format_exc()
                    self._log(f"  ⚠ 오류:\n{err}\n")
                    report_rows.append({
                        "source": src_name2, "target": dst_name, "output": dst_name,
                        "status": "오류", "filter_kept": "", "filter_removed": "",
                        "elapsed_sec": round(time.time() - start_time, 1),
                        "error": str(err).splitlines()[-1] if err else "",
                    })

                self.after(0, lambda v=done: self.progress.configure(value=v))
                out_files = sorted(_list_pdfs(output_folder))
                self.after(0, lambda fl=out_files: self._update_listbox(self.list_out, fl))

        self._write_report(report_rows)

        # 모든 작업 완료 후 Bluebeam 완전 종료
        self._log("\n[마무리] Bluebeam 종료 중…\n")
        close_bluebeam_app()

        self._log("✅ 모든 작업 완료!\n")
        self._set_status("완료 ✅", "#a6e3a1")

        if output_folder:
            os.startfile(output_folder)

    def _process_group_with_position_correction(self, src_path, targets,
                                                 filter_settings, report_rows,
                                                 done, total):
        """위치 보정 모드: Bluebeam 자동화 없이 PyMuPDF로 직접 좌표 보정하여 복사.
        done(처리 완료 개수)을 갱신해서 반환한다."""
        src_name = os.path.basename(src_path)
        self._log(f"\n▶ Source: {src_name}  ({len(targets)}개 Target) [위치 보정 모드]\n")

        open_src = src_path
        tmp_to_delete = None
        if filter_settings:
            try:
                open_src, kept, removed = build_filtered_copy(
                    src_path, "",
                    filter_settings.get("date_limit", ""),
                    filter_settings.get("author", ""),
                    log_fn=self._log,
                )
                if open_src != src_path:
                    tmp_to_delete = open_src
            except Exception:
                err = traceback.format_exc()
                self._log(f"  ⚠ 필터 적용 오류:\n{err}\n")
                for _, _, src_name2, dst_name in targets:
                    done += 1
                    report_rows.append({
                        "source": src_name2, "target": dst_name, "output": dst_name,
                        "status": "오류(필터)", "filter_kept": "", "filter_removed": "",
                        "elapsed_sec": 0, "error": str(err).splitlines()[-1] if err else "",
                    })
                self.after(0, lambda v=done: self.progress.configure(value=v))
                return done

        for dst_path, out_path, src_name2, dst_name in targets:
            done += 1
            self._log(f"  [{done}/{total}] → {dst_name}\n")
            start_time = time.time()
            try:
                copied, skipped = run_position_correction_isolated(
                    open_src, dst_path, out_path, self._log
                )
                report_rows.append({
                    "source": src_name2, "target": dst_name, "output": dst_name,
                    "status": "완료(위치보정)",
                    "filter_kept": copied, "filter_removed": skipped,
                    "elapsed_sec": round(time.time() - start_time, 1),
                    "error": "",
                })
            except Exception:
                err = traceback.format_exc()
                self._log(f"  ⚠ 오류:\n{err}\n")
                report_rows.append({
                    "source": src_name2, "target": dst_name, "output": dst_name,
                    "status": "오류", "filter_kept": "", "filter_removed": "",
                    "elapsed_sec": round(time.time() - start_time, 1),
                    "error": str(err).splitlines()[-1] if err else "",
                })

            self.after(0, lambda v=done: self.progress.configure(value=v))
            out_files = sorted(_list_pdfs(output_folder))
            self.after(0, lambda fl=out_files: self._update_listbox(self.list_out, fl))

        if tmp_to_delete:
            try:
                os.remove(tmp_to_delete)
            except OSError:
                pass

        return done

    def _write_report(self, report_rows):
        """처리 결과를 Output 폴더에 CSV로 저장"""
        if not report_rows or not output_folder:
            return
        report_path = os.path.join(output_folder, "처리결과.csv")
        try:
            with open(report_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "source", "target", "output", "status",
                    "filter_kept", "filter_removed", "elapsed_sec", "error",
                ])
                writer.writeheader()
                writer.writerows(report_rows)
            self._log(f"\n📄 처리 결과 리포트 저장: {report_path}\n")
        except OSError:
            self._log(f"\n⚠ 처리 결과 리포트 저장 실패: {report_path}\n")

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
    mp.freeze_support()
    app = App()
    app.mainloop()

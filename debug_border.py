"""
위치 보정 디버그 스크립트
Source/Target PDF 두 개를 넣으면, 도면 내용은 출력하지 않고
- 페이지 크기
- 탐지된 Border(테두리) 사각형 좌표
- 계산된 변환 행렬(이동/회전/스케일)
만 출력합니다.

사용법:
    python debug_border.py <source.pdf> <target.pdf>
"""
import sys
import math

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF가 설치되어 있지 않습니다. 'pip install PyMuPDF' 실행 후 다시 시도하세요.")
    sys.exit(1)


def edges_of_item(item):
    """item에서 (p1, p2) 직선 변들을 추출 (re는 4변으로, qu는 4변으로 분해)"""
    op = item[0]
    if op == "l":
        return [(item[1], item[2])]
    if op == "re":
        r = fitz.Rect(item[1])
        tl, tr = fitz.Point(r.x0, r.y0), fitz.Point(r.x1, r.y0)
        bl, br = fitz.Point(r.x0, r.y1), fitz.Point(r.x1, r.y1)
        return [(tl, tr), (tr, br), (br, bl), (bl, tl)]
    if op == "qu":
        try:
            pts = list(item[1])
        except Exception:
            return []
        return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    return []


def find_border_rect(page, log_prefix=""):
    page_rect = page.rect
    page_w, page_h = page_rect.width, page_rect.height
    page_area = page_w * page_h
    if page_area <= 0:
        return None

    try:
        drawings = page.get_drawings()
    except Exception as e:
        print(f"{log_prefix}  get_drawings() 실패: {e}")
        return None

    print(f"{log_prefix}  드로잉 객체 수: {len(drawings)}")

    TOL = 1.0
    LEN_FRAC = 0.7

    horiz_ys = []
    vert_xs = []
    for d in drawings:
        for item in d.get("items", []):
            for p1, p2 in edges_of_item(item):
                dx = abs(p2.x - p1.x)
                dy = abs(p2.y - p1.y)
                if dy <= TOL and dx >= page_w * LEN_FRAC:
                    horiz_ys.append((p1.y + p2.y) / 2)
                elif dx <= TOL and dy >= page_h * LEN_FRAC:
                    vert_xs.append((p1.x + p2.x) / 2)

    print(f"{log_prefix}  페이지 너비의 {LEN_FRAC*100:.0f}% 이상인 가로선: {len(horiz_ys)}개, "
          f"높이의 {LEN_FRAC*100:.0f}% 이상인 세로선: {len(vert_xs)}개")

    if horiz_ys and vert_xs:
        rect = fitz.Rect(min(vert_xs), min(horiz_ys), max(vert_xs), max(horiz_ys))
        print(f"{log_prefix}  [긴 직선 기반] 후보 Border: {rect}  (w={rect.width:.1f}, h={rect.height:.1f})")
        if rect.width > page_w * 0.3 and rect.height > page_h * 0.3:
            return rect
        print(f"{log_prefix}  → 너무 작아서 폐기, fallback 사용")

    # ── fallback ──
    MIN_FRAC, MAX_FRAC = 0.3, 0.995
    candidates = []
    re_count = 0
    for d in drawings:
        for item in d.get("items", []):
            if item[0] == "re":
                re_count += 1
                rect = fitz.Rect(item[1])
                area = abs(rect.width * rect.height)
                if page_area * MIN_FRAC <= area <= page_area * MAX_FRAC:
                    candidates.append(rect)
    bbox_count = 0
    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        bbox_count += 1
        rect = fitz.Rect(r)
        area = abs(rect.width * rect.height)
        if page_area * MIN_FRAC <= area <= page_area * MAX_FRAC:
            candidates.append(rect)

    print(f"{log_prefix}  [fallback] 명시적 사각형('re') 개수: {re_count}, path bbox 개수: {bbox_count}, "
          f"후보(면적 {MIN_FRAC*100:.0f}%~{MAX_FRAC*100:.0f}%): {len(candidates)}")

    if not candidates:
        return None

    candidates.sort(key=lambda r: r.width * r.height, reverse=True)
    print(f"{log_prefix}  상위 후보(최대 5개):")
    for i, r in enumerate(candidates[:5]):
        print(f"{log_prefix}    [{i}] {r}  (w={r.width:.1f}, h={r.height:.1f})")

    return candidates[0]


def find_border_anchor_points(doc, label):
    print(f"\n[{label}] 페이지 수: {doc.page_count}")
    for page in doc:
        print(f"[{label}] 페이지 {page.number} 크기: {page.rect}")
        rect = find_border_rect(page, log_prefix=f"[{label}]")
        if rect:
            print(f"[{label}] >>> 선택된 Border: {rect}")
            return page.number, (rect.x0, rect.y0), (rect.x1, rect.y1)
        else:
            print(f"[{label}] >>> Border 탐지 실패 (이 페이지)")
    return None


def compute_similarity_matrix(src_p1, src_p2, dst_p1, dst_p2):
    s1 = complex(*src_p1)
    s2 = complex(*src_p2)
    d1 = complex(*dst_p1)
    d2 = complex(*dst_p2)

    src_dist = abs(s2 - s1)
    dst_dist = abs(d2 - d1)
    print(f"\nSource 기준점 거리: {src_dist:.2f}")
    print(f"Target 기준점 거리: {dst_dist:.2f}")

    if src_dist < 5 or dst_dist < 5:
        print("!! 기준점 거리가 너무 가깝습니다 (< 5pt)")
        return

    scale = dst_dist / src_dist
    print(f"스케일: {scale:.4f}")

    k = (d2 - d1) / (s2 - s1)
    a, b = k.real, k.imag
    angle_deg = math.degrees(math.atan2(b, a))
    print(f"회전각: {angle_deg:.3f}도")

    e = d1.real - (a * s1.real - b * s1.imag)
    f = d1.imag - (b * s1.real + a * s1.imag)
    print(f"이동(e, f): ({e:.2f}, {f:.2f})")
    print(f"행렬: a={a:.4f}, b={b:.4f}, e={e:.2f}, f={f:.2f}")


def main():
    if len(sys.argv) != 3:
        print("사용법: python debug_border.py <source.pdf> <target.pdf>")
        sys.exit(1)

    src_path, dst_path = sys.argv[1], sys.argv[2]

    src_doc = fitz.open(src_path)
    dst_doc = fitz.open(dst_path)

    src_result = find_border_anchor_points(src_doc, "SOURCE")
    dst_result = find_border_anchor_points(dst_doc, "TARGET")

    if src_result and dst_result:
        _, src_p1, src_p2 = src_result
        _, dst_p1, dst_p2 = dst_result
        compute_similarity_matrix(src_p1, src_p2, dst_p1, dst_p2)
    else:
        print("\n!! Border를 한쪽 이상에서 찾지 못해 변환 행렬을 계산할 수 없습니다.")

    src_doc.close()
    dst_doc.close()


if __name__ == "__main__":
    main()

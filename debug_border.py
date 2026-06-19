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


def find_border_rect(page, log_prefix=""):
    page_rect = page.rect
    page_area = page_rect.width * page_rect.height
    if page_area <= 0:
        return None

    try:
        drawings = page.get_drawings()
    except Exception as e:
        print(f"{log_prefix}  get_drawings() 실패: {e}")
        return None

    print(f"{log_prefix}  드로잉 객체 수: {len(drawings)}")

    candidates = []
    re_count = 0
    for d in drawings:
        for item in d.get("items", []):
            if item[0] == "re":
                re_count += 1
                rect = fitz.Rect(item[1])
                area = abs(rect.width * rect.height)
                if 0 < area <= page_area * 0.995:
                    candidates.append(rect)

    print(f"{log_prefix}  명시적 사각형('re') 개수: {re_count}, 후보(전체 페이지 제외): {len(candidates)}")

    if not candidates:
        for d in drawings:
            r = d.get("rect")
            if r is None:
                continue
            rect = fitz.Rect(r)
            area = abs(rect.width * rect.height)
            if 0 < area <= page_area * 0.995:
                candidates.append(rect)
        print(f"{log_prefix}  fallback bbox 후보: {len(candidates)}")

    if not candidates:
        return None

    candidates.sort(key=lambda r: r.width * r.height, reverse=True)

    # 상위 5개 후보 크기 출력 (어떤 사각형이 선택되는지 확인용)
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

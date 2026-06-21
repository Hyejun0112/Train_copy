"""
위치 보정 디버그 스크립트
Source/Target PDF 두 개를 넣으면, 도면 내용은 출력하지 않고
- 페이지 수/크기
- Train 번호만 다른 동일 Tag 매칭 결과(Tag 텍스트, 좌표)
- 계산된 변환 행렬(이동/회전/스케일)
만 출력합니다.

사용법:
    python debug_border.py <source.pdf> <target.pdf>
"""
import sys
import re
import math

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF가 설치되어 있지 않습니다. 'pip install PyMuPDF' 실행 후 다시 시도하세요.")
    sys.exit(1)


# 사용자가 Bluebeam에서 직접 찍어두는 마젠타색 수동 기준점 마크업
MANUAL_ANCHOR_COLOR = (1.0, 0.0, 1.0)
MANUAL_ANCHOR_TOL = 0.08


def is_anchor_color(annot):
    colors = annot.colors or {}
    for c in (colors.get("stroke"), colors.get("fill")):
        if c and len(c) == 3 and all(
            abs(c[i] - MANUAL_ANCHOR_COLOR[i]) < MANUAL_ANCHOR_TOL for i in range(3)
        ):
            return True
    return False


def extract_manual_anchor_points(page, log_prefix=""):
    found = []
    for annot in page.annots() or []:
        if not is_anchor_color(annot):
            continue
        r = annot.rect
        found.append((annot.xref, ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)))
    print(f"{log_prefix}  마젠타 수동 기준점 마크업: {len(found)}개 발견")
    return found


# Train 번호(앞쪽 2~4자리 숫자)만 다르고 나머지는 동일한 Tag 패턴
# 예: "504-CWS-0100-400-ACB3B02SE51-NN" / "604-CWS-0100-400-ACB3B02SE51-NN"
TAG_RE = re.compile(r'^(\d{2,4})-([A-Za-z0-9][A-Za-z0-9-]{4,})$')


def extract_tag_suffixes(page, log_prefix=""):
    suffix_map = {}
    dup_count = 0
    try:
        words = page.get_text("words")
    except Exception as e:
        print(f"{log_prefix}  get_text('words') 실패: {e}")
        return {}

    print(f"{log_prefix}  추출된 단어(word) 수: {len(words)}")

    for w in words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        m = TAG_RE.match(text)
        if not m:
            continue
        suffix = m.group(2)
        center = ((x0 + x1) / 2, (y0 + y1) / 2)
        if suffix in suffix_map:
            if suffix_map[suffix] is not None:
                dup_count += 1
            suffix_map[suffix] = None
        else:
            suffix_map[suffix] = center

    result = {k: v for k, v in suffix_map.items() if v is not None}
    print(f"{log_prefix}  Tag 패턴에 매칭된 단어: {sum(1 for v in suffix_map.values()) }개 항목, "
          f"고유 suffix: {len(result)}개, 중복(모호함)으로 제외: {dup_count}개")
    return result


# 계측기/제어밸브/On-off 밸브/Logic/OPC 등 일반 Tag (예: "PI-0101", "CV-0022",
# "XV-0011") — Train 번호와 무관하게 동일한 문자가 그대로 유지되므로 기준점
# 밀도를 늘리는 데 쓴다.
# 단, "수동(Manual) 밸브" Tag(GV, BFV, BV, GLV, PLV, NRV, CKV, DV 등)는
# Train Copy마다 번호가 별도로 매겨지므로 제외한다. Control Valve(CV)나
# On/off Valve(XV, SOV, AOV, MOV 등)는 사용 가능.
GENERIC_TAG_RE = re.compile(r'^[A-Za-z]{1,6}-\d{2,6}[A-Za-z0-9]*$')
MANUAL_VALVE_PREFIX_RE = re.compile(
    r'^(?:GLV|PLV|NRV|CKV|BFV|GV|BV|DV)-', re.IGNORECASE
)


def extract_generic_tags(page, log_prefix=""):
    text_map = {}
    dup_count = 0
    try:
        words = page.get_text("words")
    except Exception as e:
        print(f"{log_prefix}  get_text('words') 실패: {e}")
        return {}

    for w in words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        if not GENERIC_TAG_RE.match(text):
            continue
        if MANUAL_VALVE_PREFIX_RE.match(text):
            continue
        center = ((x0 + x1) / 2, (y0 + y1) / 2)
        if text in text_map:
            if text_map[text] is not None:
                dup_count += 1
            text_map[text] = None
        else:
            text_map[text] = center

    result = {k: v for k, v in text_map.items() if v is not None}
    print(f"{log_prefix}  일반 Tag 패턴에 매칭된 단어: {sum(1 for v in text_map.values())}개 항목, "
          f"고유 텍스트: {len(result)}개, 중복(모호함)으로 제외: {dup_count}개")
    return result


# Symbol(벡터 도형) 기준점: 작은 벡터 도형 묶음의 모양 시그니처가 페이지에 단 한
# 번만 나오는 경우만 기준점으로 사용한다.
SYMBOL_MAX_SIZE = 60  # pt


def extract_symbol_signatures(page, log_prefix=""):
    try:
        drawings = page.get_drawings()
    except Exception as e:
        print(f"{log_prefix}  get_drawings() 실패: {e}")
        return {}
    sig_map = {}
    dup_count = 0
    for d in drawings:
        rect = d.get("rect")
        if rect is None or rect.width <= 0 or rect.height <= 0:
            continue
        if rect.width > SYMBOL_MAX_SIZE or rect.height > SYMBOL_MAX_SIZE:
            continue
        items = d.get("items", [])
        sig = (len(items), round(rect.width, 1), round(rect.height, 1))
        center = ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        if sig in sig_map:
            if sig_map[sig] is not None:
                dup_count += 1
            sig_map[sig] = None
        else:
            sig_map[sig] = center
    result = {k: v for k, v in sig_map.items() if v is not None}
    print(f"{log_prefix}  Symbol 후보(작은 벡터 도형): {len(drawings)}개 중 크기 필터 통과, "
          f"고유 시그니처: {len(result)}개, 중복(모호함)으로 제외: {dup_count}개")
    return result


def main():
    if len(sys.argv) != 3:
        print("사용법: python debug_border.py <source.pdf> <target.pdf>")
        sys.exit(1)

    src_path, dst_path = sys.argv[1], sys.argv[2]

    src_doc = fitz.open(src_path)
    dst_doc = fitz.open(dst_path)

    print(f"[SOURCE] 페이지 수: {src_doc.page_count}")
    print(f"[TARGET] 페이지 수: {dst_doc.page_count}")

    best = None
    for sp in src_doc:
        src_anchors = extract_manual_anchor_points(sp, log_prefix="[SOURCE]")
        for dp in dst_doc:
            dst_anchors = extract_manual_anchor_points(dp, log_prefix="[TARGET]")
            if src_anchors and dst_anchors and len(src_anchors) == len(dst_anchors):
                src_sorted = sorted(src_anchors, key=lambda t: t[0])
                dst_sorted = sorted(dst_anchors, key=lambda t: t[0])
                print(f"  >>> 수동 기준점 {len(src_sorted)}개 매칭 (이것만 사용)")
                best = (sp.number, dp.number,
                        [("수동기준점", s[1], d[1]) for s, d in zip(src_sorted, dst_sorted)])
                break
        if best:
            break

    if not best:
        for sp in src_doc:
            print(f"\n[SOURCE] 페이지 {sp.number} 크기: {sp.rect}")
            src_map = extract_tag_suffixes(sp, log_prefix="[SOURCE]")
            src_generic = extract_generic_tags(sp, log_prefix="[SOURCE]")
            src_symbols = extract_symbol_signatures(sp, log_prefix="[SOURCE]")
            if not src_map and not src_generic and not src_symbols:
                continue
            for dp in dst_doc:
                print(f"[TARGET] 페이지 {dp.number} 크기: {dp.rect}")
                dst_map = extract_tag_suffixes(dp, log_prefix="[TARGET]")
                dst_generic = extract_generic_tags(dp, log_prefix="[TARGET]")
                dst_symbols = extract_symbol_signatures(dp, log_prefix="[TARGET]")
                common = sorted(set(src_map) & set(dst_map))
                common_generic = sorted(set(src_generic) & set(dst_generic))
                common_symbols = sorted(set(src_symbols) & set(dst_symbols), key=str)
                total = len(common) + len(common_generic) + len(common_symbols)
                print(f"  >>> 공통 매칭: 배관선번호 {len(common)}개 + 일반 Tag {len(common_generic)}개 + "
                      f"Symbol {len(common_symbols)}개 = {total}개")
                if total >= 2:
                    matches = [(s, src_map[s], dst_map[s]) for s in common]
                    matches += [(s, src_generic[s], dst_generic[s]) for s in common_generic]
                    matches += [(s, src_symbols[s], dst_symbols[s]) for s in common_symbols]
                    best = (sp.number, dp.number, matches)
                    break
            if best:
                break

    if not best:
        print("\n!! 매칭된 Tag가 2개 미만이라 변환 행렬을 계산할 수 없습니다.")
        print("   - Tag 텍스트가 PDF에서 텍스트로 추출되는지(이미지로 스캔된 도면이 아닌지) 확인하세요.")
        print("   - Tag 형식이 'NNN-나머지' 패턴(앞 2~4자리 숫자 + '-' + 나머지)과 맞는지 확인하세요.")
        src_doc.close()
        dst_doc.close()
        return

    src_page_idx, dst_page_idx, matches = best
    print(f"\n매칭된 Tag 목록 (Source p{src_page_idx+1} → Target p{dst_page_idx+1}):")
    for suffix, sp_pt, dp_pt in matches:
        print(f"  - ...{suffix}  Source{tuple(round(v,1) for v in sp_pt)} -> "
              f"Target{tuple(round(v,1) for v in dp_pt)}")

    pairs = [(sp_pt, dp_pt) for _, sp_pt, dp_pt in matches]
    s = [complex(*p[0]) for p in pairs]
    d = [complex(*p[1]) for p in pairs]
    n = len(s)
    s_mean = sum(s) / n
    d_mean = sum(d) / n
    den = sum(abs(si - s_mean) ** 2 for si in s)
    if den < 25:
        print("\n!! 매칭된 Tag들의 위치가 서로 너무 가깝습니다.")
    else:
        num = sum((si - s_mean).conjugate() * (di - d_mean) for si, di in zip(s, d))
        k = num / den
        t = d_mean - k * s_mean
        a, b = k.real, k.imag
        e, f = t.real, t.imag
        scale = abs(k)
        angle_deg = math.degrees(math.atan2(b, a))
        residuals = [abs(k * si + t - di) for si, di in zip(s, d)]
        print(f"\n스케일: {scale:.4f}")
        print(f"회전각: {angle_deg:.3f}도")
        print(f"이동(e, f): ({e:.2f}, {f:.2f})")
        print(f"행렬: a={a:.4f}, b={b:.4f}, e={e:.2f}, f={f:.2f}")
        print(f"잔차(각 매칭점 오차, pt): {[round(r,2) for r in residuals]}")

    src_doc.close()
    dst_doc.close()


if __name__ == "__main__":
    main()

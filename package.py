"""
배포용 zip 패키징 스크립트
사용법: python package.py        (먼저 python build.py 로 EXE를 만든 뒤 실행)

dist/Train_copy.exe + 사용법 안내 + 매핑양식 엑셀을 묶어
dist/Train_copy_v{버전}.zip 을 만든다. 이 zip 하나만 배포하면 된다.
"""

import os
import sys
import zipfile

VERSION  = "1.0"
APP_NAME = "Train_copy"

EXE_PATH      = os.path.join("dist", f"{APP_NAME}.exe")
TEMPLATE_XLSX = "Train_copy_매핑양식.xlsx"   # 있으면 같이 포함(없으면 건너뜀)
ZIP_PATH      = os.path.join("dist", f"{APP_NAME}_v{VERSION}.zip")

USAGE_TEXT = f"""Train_copy v{VERSION} — 사용법

[설치]
1. 이 zip 파일의 압축을 푸세요(반드시 풀고 실행해야 합니다).
2. 압축 안에서 바로 더블클릭하지 마세요 — 느리거나 오류가 날 수 있습니다.

[실행]
- Train_copy.exe 를 더블클릭하면 됩니다.
- 처음 실행 시 "Windows의 PC 보호" 파란 경고가 뜨면,
  [추가 정보] → [실행] 을 누르세요. (서명되지 않은 프로그램이라 뜨는 정상 경고)
- 백신이 차단하면 사내 IT에 화이트리스트(예외) 등록을 요청하세요.

[매핑양식]
- Source/Target 매핑 기능을 쓸 때는 동봉된
  '{TEMPLATE_XLSX}' 파일을 양식으로 사용하세요.

문의: 배포자에게 작업 로그(화면의 로그 전체)와 함께 문의하세요.
"""


def main():
    if not os.path.isfile(EXE_PATH):
        print(f"❌ {EXE_PATH} 가 없습니다. 먼저 'python build.py' 로 EXE를 만드세요.")
        sys.exit(1)

    os.makedirs("dist", exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        # 1) 실행 파일
        z.write(EXE_PATH, arcname=f"{APP_NAME}.exe")
        # 2) 사용법 안내
        z.writestr("사용법.txt", USAGE_TEXT)
        # 3) 매핑양식(있을 때만)
        if os.path.isfile(TEMPLATE_XLSX):
            z.write(TEMPLATE_XLSX, arcname=TEMPLATE_XLSX)
            print(f"   + 매핑양식 포함: {TEMPLATE_XLSX}")
        else:
            print(f"   (매핑양식 {TEMPLATE_XLSX} 없음 — 건너뜀)")

    size_mb = os.path.getsize(ZIP_PATH) / (1024 * 1024)
    print(f"\n✅ 배포 패키지 생성: {ZIP_PATH}  ({size_mb:.1f} MB)")
    print("   이 zip 파일 하나만 배포하면 됩니다.")


if __name__ == "__main__":
    main()

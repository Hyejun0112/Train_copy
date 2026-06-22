# 안정판 백업 (Stable Releases)

이 폴더에는 "잘 동작 확인된" 버전을 **동결(freeze)** 해 둔다.
앞으로 `Train_copy.py`(개발 버전)를 계속 수정해도 이 파일들은 건드리지 않으므로,
배포본이 깨졌을 때 언제든 되돌릴 수 있다.

## 파일

| 파일 | 설명 |
|------|------|
| `Train_copy_stable_v1.0.py` | 기울어짐 해결 + 위치보정(IDW) + Bluebeam 마크업 정상 표시 + CAD 형상 스냅(원/선) 포함. 배포 안정판. |

## 되돌리는 법

배포 버전이 문제가 생기면 안정판으로 빌드:

```
# 안정판을 메인으로 복원
copy releases\Train_copy_stable_v1.0.py Train_copy.py
python build.py
```

## 새 안정판 동결하는 법

실제 도면에서 충분히 검증된 새 버전이 나오면, 버전 번호를 올려 복사해 둔다:

```
copy Train_copy.py releases\Train_copy_stable_v1.1.py
```

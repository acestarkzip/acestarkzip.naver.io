# 자동 초안 갱신 수정 적용 안내

## 덮어쓸 파일

압축을 푼 뒤 아래 파일을 저장소 루트 기준 동일한 경로에 덮어씁니다.

- `.github/workflows/naver-blog.yml`
- `scripts/exporter.py`
- `tests/test_exporter.py`
- `README.md`
- `.env.example`
- `TOC_FIX_NOTES.md`

## 보존할 항목

- `Naver_Drafts/` 전체
- `Naver_Drafts/processed_pages.json`
- `.env`
- GitHub Secrets

`processed_pages.json`을 비우거나 삭제하지 마세요.

## 적용 후 동작

1. `Week` 실행 화면에는 조회 범위 선택 항목만 남습니다. `refresh_existing` 체크박스는 필요하지 않습니다.
2. 기존 처리 이력에 콘텐츠 해시가 없는 페이지는 선택 범위에서 한 번 재생성되며 해시를 기록합니다.
3. 이후 Notion 페이지 속성 또는 본문 블록 내용이 달라지면 자동 갱신합니다.
4. 초안 폴더나 필수 출력 파일이 누락된 페이지는 처리 이력이 남아 있어도 자동 복구합니다.
5. 내용과 출력 파일이 모두 그대로면 건너뜁니다.
6. `status`가 `published`인 페이지는 재생성하지 않습니다.

`Week`는 Notion의 `날짜` 속성 기준 최근 7일(오늘 포함)을 조회합니다. 게시일이 그보다 오래된 글의 변경은 일요일 `EDITED_WEEK` 예약 실행(최근 14일 내 수정 페이지) 또는 `ALL` 조회를 이용하세요.

## 테스트

로컬 검증: `python -m pytest -q` — 73 passed.

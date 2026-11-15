# SSS Robotics | Notion → Naver Blog Draft Automation

Notion 데이터베이스에서 **한국시간 기준 최근 3일(오늘 포함)과 내일 날짜**에 해당하는 게시글만 확인해 네이버 블로그용 초안을 생성합니다.

- 실행 환경: GitHub Actions (완전 클라우드 실행, 로컬 PC 불필요)
- 실행 주기: 매일 07:17 및 19:17 KST
- 기존 `notion-to-github` 저장소와 워크플로는 수정하지 않습니다.
- Notion 속성명 기본값: `제목`, `날짜`, `간단메모`, `태그`
- Notion 페이지 ID로 중복 생성 방지
- 본문/이미지는 검색 날짜 범위에 해당하는 페이지에 대해서만 다운로드
- 처리 이력은 `Naver_Drafts/processed_pages.json`에 자동 저장
- 이 저장소는 Public이므로 저장된 초안과 이미지도 공개됩니다.

## GitHub 설정

Repository Settings → Secrets and variables → Actions에 다음 두 Secret이 필요합니다.

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`

Notion 데이터베이스는 해당 Integration과 공유되어 있어야 합니다.

## 날짜 범위(B 방식)

예를 들어 한국시간 오늘이 2026-11-15라면 2026-11-13부터 2026-11-16까지의 Notion `날짜` 속성 게시글을 확인합니다. 즉, 오늘 포함 과거 2일과 내일까지 포함합니다. 이전 실행 실패로 누락된 최근 게시글을 다음 실행에서 다시 발견할 수 있습니다.

데이터베이스의 페이지 목록 메타데이터는 조회하지만, 날짜 범위 밖 페이지의 본문과 이미지는 가져오지 않습니다. 날짜 속성이 비어 있거나 날짜 형식이 잘못된 페이지는 건너뜁니다.

## 최초 실행

별도의 `seed_tracker.py` 또는 로컬 `.env` 설정이 필요하지 않습니다. 첫 GitHub Actions 실행부터 날짜 범위 내 게시글만 확인하고, 처리 이력을 자동 생성합니다. 따라서 과거 전체 게시글을 일괄 초안화하지 않습니다.

Actions → Naver Blog Drafts → Run workflow에서 수동 실행할 수 있습니다.

## 생성되는 초안

`Naver_Drafts/날짜_제목/` 아래에 다음 파일이 생성됩니다.

- `01_네이버_원고.html`
- `02_네이버_원고.txt`
- `03_이미지_목록.txt`
- `04_발행_체크리스트.txt`
- `metadata.json`
- `images/`

## 네이버 편집기 입력

GitHub Actions는 사용자의 로컬 PC에서 로그인된 네이버 브라우저를 조작할 수 없습니다. 이 프로젝트는 클라우드에서 초안을 생성합니다. 사용자는 저장소에서 원고와 이미지를 내려받아 네이버 에디터에 반영하고 최종 검토 후 직접 발행합니다.

## 설정 옵션

| 변수 | 기본값 | 설명 |
|---|---|---|
| `NOTION_PROPERTY_TITLE` | `제목` | 제목 속성 |
| `NOTION_PROPERTY_DATE` | `날짜` | 날짜 속성 |
| `NOTION_PROPERTY_MEMO` | `간단메모` | 메모 속성 |
| `NOTION_PROPERTY_TAGS` | `태그` | 태그 속성 |
| `UPDATE_EXISTING_DRAFTS` | `false` | 이미 처리한 미발행 초안 업데이트 허용 |
| `MAX_DOWNLOAD_MB` | `50` | 파일당 다운로드 제한 |



<!--
==================================
"processed_pages.json" 초기화 명령어
==================================
{
  "version": 1,
  "pages": {}
}

 >
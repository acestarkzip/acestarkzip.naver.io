# SSS Robotics | Notion → Naver Blog Draft Automation

Notion 데이터베이스의 신규 페이지를 정기 확인하여 네이버 블로그용 초안 패키지를 생성합니다.

- 기존 `notion-to-github` 저장소 및 워크플로는 수정하지 않습니다.
- 기존 Notion 속성명 `제목`, `날짜`, `간단메모`, `태그`를 사용합니다.
- Notion 본문과 중첩 블록을 읽고 이미지 파일을 내려받습니다.
- Notion page ID로 중복을 방지합니다.
- 기본적으로 기존 초안을 덮어쓰지 않습니다.
- 이 저장소는 Public이므로 저장된 초안과 이미지도 공개됩니다.

## 설치
GitHub Secrets에 `NOTION_TOKEN`, `NOTION_DATABASE_ID`를 등록하고 Notion 데이터베이스를 integration과 공유하세요.

## 첫 실행 주의
첫 실행은 데이터베이스의 기존 페이지도 모두 초안으로 생성합니다. 앞으로 작성하는 글만 처리하려면 로컬에서 `.env`를 설정한 뒤 `python scripts/seed_tracker.py`를 한 번 실행하고 `Naver_Drafts/processed_pages.json`을 커밋하세요.

## 로컬에서 초안 열기
`pip install -r requirements.txt` 후 `python scripts/open_draft.py`를 실행합니다. `.env`에 `NAVER_BLOG_ID`를 설정하면 글쓰기 페이지로 이동합니다.

GitHub Actions는 사용자의 PC에 로그인된 네이버 브라우저를 조작할 수 없습니다. 이 버전은 정기 초안 생성과 로컬 편집기 열기를 분리합니다. 네이버 에디터에 이미지 파일을 직접 업로드하고 최종 검토/발행은 직접 진행하세요.

## 생성물
`Naver_Drafts/날짜_제목/` 아래에 HTML 미리보기, 텍스트 원고, 이미지 목록, 발행 체크리스트, metadata.json 및 images/가 생성됩니다.

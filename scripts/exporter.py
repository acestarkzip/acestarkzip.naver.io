from __future__ import annotations

import os
import re
import json
import mimetypes
from pathlib import Path
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, unquote

import requests

from notion_reader import query_database, get_blocks, load_tree
from content_converter import plain, render_blocks, wrap_html

ROOT = Path(__file__).resolve().parents[1]
DRAFTS = ROOT / "Naver_Drafts"
TRACKER = DRAFTS / "processed_pages.json"

TITLE_PROP = os.getenv("NOTION_PROPERTY_TITLE", "제목")
DATE_PROP = os.getenv("NOTION_PROPERTY_DATE", "날짜")
MEMO_PROP = os.getenv("NOTION_PROPERTY_MEMO", "간단메모")
TAGS_PROP = os.getenv("NOTION_PROPERTY_TAGS", "태그")
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "50")) * 1024 * 1024
UPDATE_EXISTING = os.getenv("UPDATE_EXISTING_DRAFTS", "false").lower() == "true"
KST = ZoneInfo("Asia/Seoul")


def title_of(page: dict) -> str:
    props = page.get("properties", {})
    title = plain((props.get(TITLE_PROP) or {}).get("title") or [])
    if not title:
        for prop in props.values():
            if prop.get("type") == "title":
                title = plain(prop.get("title") or [])
                break
    return title.strip() or "제목없음"


def date_of(page: dict) -> date | None:
    value = (page.get("properties", {}).get(DATE_PROP, {}).get("date") or {}).get("start")
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        print(f"[SKIP] 날짜 형식 확인 필요: {value}")
        return None


def memo_of(page: dict) -> str:
    prop = page.get("properties", {}).get(MEMO_PROP, {})
    return plain(prop.get("rich_text") or prop.get("title") or []).strip()


def tags_of(page: dict) -> list[str]:
    prop = page.get("properties", {}).get(TAGS_PROP, {})
    return [
        item["name"].strip()
        for item in prop.get("multi_select", [])
        if item.get("name", "").strip()
    ]


def safe_name(value: str) -> str:
    value = re.sub(r'[\\/*?:"<>|]', "", value)
    value = re.sub(r"\s+", "_", value).strip("._")
    return value[:90] or "untitled"


def media_blocks(blocks: list[dict]) -> list[dict]:
    found = []
    for block in blocks:
        if block.get("type") in ("image", "file", "pdf"):
            found.append(block)
        found.extend(media_blocks(block.get("_children") or []))
    return found


def save_media(block: dict, folder: Path, index: int) -> Path | None:
    data = block.get(block.get("type"), {}) or {}
    media = data.get("file") or data.get("external") or {}
    url = media.get("url")
    if not url or not url.startswith("https://"):
        return None

    folder.mkdir(parents=True, exist_ok=True)
    original_name = data.get("name") or f"media_{index}"
    extension = Path(unquote(urlparse(url).path)).suffix.lower()
    if extension not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf"):
        extension = mimetypes.guess_extension(media.get("mime_type", "")) or ".bin"

    destination = folder / f"{index:02d}_{safe_name(Path(original_name).stem)}{extension}"
    try:
        with requests.get(url, stream=True, timeout=30) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("Content-Length", "0") or 0)
            if content_length > MAX_DOWNLOAD_BYTES:
                print(f"[SKIP] 파일 크기 제한 초과: {original_name}")
                return None

            total = 0
            with destination.open("wb") as output:
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError("파일 크기 제한 초과")
                    output.write(chunk)

        if destination.stat().st_size == 0:
            destination.unlink(missing_ok=True)
            return None
        return destination
    except Exception as error:
        destination.unlink(missing_ok=True)
        print(f"[WARN] 미디어 다운로드 실패 ({original_name}): {error}")
        return None


def date_window(today: date) -> tuple[date, date]:
    # 테스트 모드: 한국시간 기준 오늘 날짜의 글만 처리
    return today, today


def main() -> None:
    DRAFTS.mkdir(parents=True, exist_ok=True)
    if TRACKER.exists():
        tracker = json.loads(TRACKER.read_text(encoding="utf-8"))
    else:
        tracker = {"version": 1, "pages": {}}

    processed = tracker.setdefault("pages", {})
    today_kst = datetime.now(KST).date()
    start_date, end_date = date_window(today_kst)
    # Notion API 단계에서 오늘 날짜에 해당하는 페이지만 조회합니다.
    pages = query_database(date_property=DATE_PROP, on_date=today_kst.isoformat())
    candidates = []

    # 안전을 위해 반환된 페이지의 날짜도 한 번 더 확인합니다.
    for page in pages:
        page_date = date_of(page)
        if page_date is None:
            print(f"[SKIP] 날짜 속성이 없거나 올바르지 않은 페이지: {title_of(page)}")
            continue
        if not start_date <= page_date <= end_date:
            continue
        candidates.append((page_date, page))

    candidates.sort(key=lambda item: (item[0], title_of(item[1])))
    print(
        f"[INFO] 한국시간 오늘 날짜 {today_kst.isoformat()} 대상: "
        f"{len(candidates)}개"
    )
    created = 0
    skipped = 0

    for page_date, page in candidates:
        page_id = page["id"]
        last_edited = page.get("last_edited_time", "")
        previous = processed.get(page_id)

        if previous and previous.get("status") == "published":
            print(f"[SKIP] 발행 완료 페이지 보호: {title_of(page)}")
            skipped += 1
            continue

        if previous and not UPDATE_EXISTING:
            print(f"[SKIP] 이미 처리한 페이지: {title_of(page)}")
            skipped += 1
            continue

        # When update_existing is enabled, regenerate every un-published draft
        # in the scan window, even if Notion's last_edited_time is unchanged.
        # This also supports re-rendering after converter/template improvements.

        title = title_of(page)
        memo = memo_of(page)
        tags = tags_of(page)
        folder_name = safe_name(f"{page_date.isoformat()}_{title}")
        folder = DRAFTS / folder_name

        if folder.exists() and (not previous or previous.get("folder") != folder_name):
            folder_name = f"{folder_name}_{page_id.replace('-', '')[:6]}"
            folder = DRAFTS / folder_name

        blocks = load_tree(get_blocks(page_id))
        image_map = {}
        image_names = []

        for index, block in enumerate(media_blocks(blocks), start=1):
            saved = save_media(block, folder / "images", index)
            if not saved:
                continue
            image_names.append(saved.name)
            if block.get("type") == "image":
                image_map[block["id"]] = f"images/{saved.name}"

        folder.mkdir(parents=True, exist_ok=True)
        text_lines = []
        body_html = render_blocks(blocks, image_map, text_lines, code_image_dir=folder / "images")

        (folder / "01_네이버_원고.html").write_text(
            wrap_html(title, memo, body_html, tags), encoding="utf-8"
        )
        text_content = "\n".join([
            title,
            "=" * min(len(title), 60),
            memo,
            "",
            *text_lines,
            "",
            " ".join("#" + tag for tag in tags),
        ])
        (folder / "02_네이버_원고.txt").write_text(text_content, encoding="utf-8")
        (folder / "03_이미지_목록.txt").write_text(
            "\n".join(image_names) if image_names else "다운로드된 이미지가 없습니다.",
            encoding="utf-8",
        )

        checklist = [
            "네이버 블로그 발행 전 체크리스트",
            "[ ] 제목과 본문 오탈자 확인",
            "[ ] 이미지 위치와 표시 확인",
            "[ ] 이미지 출처 및 사용 권한 확인",
            "[ ] 링크 확인",
            "[ ] 태그 입력",
            "[ ] 공개 범위 확인",
            "[ ] 미리보기 후 직접 발행",
        ]
        (folder / "04_발행_체크리스트.txt").write_text(
            "\n".join(checklist), encoding="utf-8"
        )

        metadata = {
            "notion_page_id": page_id,
            "title": title,
            "date": page_date.isoformat(),
            "memo": memo,
            "tags": tags,
            "last_edited_time": last_edited,
            "notion_url": page.get("url"),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "status": "draft",
        }
        (folder / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        processed[page_id] = {
            "folder": folder_name,
            "last_edited_time": last_edited,
            "status": "draft",
            "title": title,
            "date": page_date.isoformat(),
        }
        created += 1
        print(f"[DRAFT] {folder_name}")

    tracker["last_scan"] = datetime.now(timezone.utc).isoformat()
    tracker["scan_window"] = {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "timezone": "Asia/Seoul",
    }
    TRACKER.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] 신규/갱신 초안 {created}개, 건너뜀 {skipped}개")


if __name__ == "__main__":
    main()

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
from content_converter import plain, render_blocks, render_plain_text, wrap_html

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
        images_folder = folder / "images"
        if images_folder.exists():
            for stale_code_image in images_folder.glob("code_*.png"):
                stale_code_image.unlink()
        image_map = {}
        image_names = []

        for index, block in enumerate(media_blocks(blocks), start=1):
            saved = save_media(block, folder / "images", index)
            if not saved:
                continue
            image_names.append(saved.name)
            if block.get("id"):
                image_map[block["id"]] = f"images/{saved.name}"

        folder.mkdir(parents=True, exist_ok=True)
        text_lines = []
        headings = []
        body_html = render_blocks(
            blocks, image_map, text_lines,
            code_image_dir=folder / "images",
            headings=headings,
        )
        # Include generated code screenshots as well as downloaded Notion media.
        code_images = sorted((folder / "images").glob("code_*.png"))
        image_names.extend(path.name for path in code_images if path.name not in image_names)

        # TXT files are the primary publishing artifacts for Naver SmartEditor.
        # HTML is kept only as an optional visual preview, never as the copy source.
        body_text = render_plain_text(blocks, image_map, headings)
        (folder / "01_네이버_제목.txt").write_text(title.strip() + "\n", encoding="utf-8")

        body_parts = [body_text.strip()]
        if tags:
            body_parts.extend(["", " ".join("#" + tag for tag in tags)])
        (folder / "02_네이버_본문.txt").write_text(
            "\n".join(part for part in body_parts if part is not None).strip() + "\n",
            encoding="utf-8",
        )

        image_list_lines = [
            "네이버 블로그 이미지 업로드 목록",
            "",
            "본문의 [이미지 삽입 위치: ...] 표시를 찾아 해당 파일을 업로드하세요.",
            "이미지는 본문에 표시된 순서대로 삽입하면 됩니다.",
            "",
        ]
        # Match the checklist order to the image markers as they appear in the body.
        ordered_image_names = sorted(
            image_names,
            key=lambda name: body_text.find(f"images/{name}")
            if body_text.find(f"images/{name}") >= 0 else len(body_text) + image_names.index(name),
        )
        image_list_lines.extend(
            f"{index:02d}. images/{name}"
            for index, name in enumerate(ordered_image_names, start=1)
        )
        if not image_names:
            image_list_lines.append("다운로드된 이미지가 없습니다.")
        (folder / "03_이미지_목록.txt").write_text(
            "\n".join(image_list_lines) + "\n", encoding="utf-8"
        )

        # Keep original code in a separate optional file. The main article
        # points to its PNG at the exact insertion location.
        code_sources: list[dict[str, str]] = []
        render_plain_text(blocks, image_map, headings, code_sources)
        code_backup_path = folder / "05_코드_원문.txt"
        if code_backup_path.exists() and not code_sources:
            code_backup_path.unlink()
        if code_sources:
            code_lines = ["코드 원문 백업 (필요한 경우에만 사용)", ""]
            for index, item in enumerate(code_sources, start=1):
                code_lines.extend([
                    f"[{index}] {item['language'].upper()} 코드",
                    f"이미지 파일: images/{item['filename']}",
                    "",
                    item["code"].rstrip(),
                    "",
                    "────────────────────────",
                    "",
                ])
            (folder / "05_코드_원문.txt").write_text(
                "\n".join(code_lines).rstrip() + "\n", encoding="utf-8"
            )

        checklist = [
            "네이버 블로그 발행 전 체크리스트",
            "[ ] 01_네이버_제목.txt의 제목을 네이버 제목란에 입력",
            "[ ] 02_네이버_본문.txt의 내용을 네이버 본문에 붙여넣기",
            "[ ] 본문에 표시된 이미지 삽입 위치에 images 폴더의 파일 업로드",
            "[ ] 코드 이미지가 모두 삽입되었는지 확인",
            "[ ] 링크가 정상적으로 표시되는지 확인",
            "[ ] 해시태그 확인 및 필요 시 네이버 태그 입력란에도 등록",
            "[ ] 오탈자와 줄바꿈 확인",
            "[ ] 미리보기 후 직접 발행",
        ]
        (folder / "04_발행_체크리스트.txt").write_text(
            "\n".join(checklist) + "\n", encoding="utf-8"
        )

        usage_guide = [
            "네이버 블로그 원고 사용 방법",
            "",
            "이 폴더의 TXT 파일이 네이버 블로그에 사용할 기본 원고입니다.",
            "HTML 파일은 미리보기 전용이며, HTML에서 본문을 복사하지 마세요.",
            "",
            "1. GitHub에서 해당 초안 폴더 전체를 다운로드하고 압축을 풉니다.",
            "2. 01_네이버_제목.txt를 열어 제목 한 줄을 복사한 뒤 네이버 제목란에 붙여넣습니다.",
            "3. 02_네이버_본문.txt를 메모장 또는 일반 텍스트 편집기로 열어 본문을 복사합니다.",
            "4. 네이버 블로그 본문 편집기에 붙여넣습니다. 제목·강조·인용 서식은 네이버 편집기에서 적용합니다.",
            "5. 본문에 표시된 [이미지 삽입 위치: images/파일명] 문구를 찾아 지우고, 그 위치에 사진 첨부 기능으로 이미지를 올립니다.",
            "6. 코드 이미지는 같은 방식으로 images 폴더의 code_*.png 파일을 올립니다.",
            "7. 링크와 해시태그를 확인하고 미리보기 후 직접 발행합니다.",
            "",
            "주의: 로컬 이미지 파일은 텍스트 붙여넣기만으로 네이버에 업로드되지 않습니다. images 폴더의 파일을 사진 첨부 기능으로 직접 등록해야 합니다.",
            "06_미리보기.html은 참고용 화면이며, 복사·붙여넣기 원고로 사용하지 않습니다.",
        ]
        (folder / "00_네이버_블로그_사용방법.txt").write_text(
            "\n".join(usage_guide) + "\n", encoding="utf-8"
        )

        (folder / "06_미리보기.html").write_text(
            wrap_html(title, body_html, tags, headings), encoding="utf-8"
        )

        # Remove files generated by earlier versions to avoid selecting the
        # obsolete HTML-copy workflow by accident.
        for obsolete_name in (
            "01_네이버_원고.html", "02_네이버_원고.txt",
            "03_이미지_목록.txt", "04_발행_체크리스트.txt",
        ):
            obsolete = folder / obsolete_name
            if obsolete.exists():
                obsolete.unlink()

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

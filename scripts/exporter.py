from __future__ import annotations

import os
import re
import json
import hashlib
import mimetypes
import zipfile
from pathlib import Path
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, unquote

import requests

from notion_reader import query_database, get_blocks, load_tree
from content_converter import (
    plain, render_blocks, render_plain_text, wrap_html, render_code_image,
    code_image_filename,
)
from docx_exporter import write_naver_docx

ROOT = Path(__file__).resolve().parents[1]
DRAFTS = ROOT / "Naver_Drafts"
TRACKER = DRAFTS / "processed_pages.json"

TITLE_PROP = os.getenv("NOTION_PROPERTY_TITLE", "제목")
DATE_PROP = os.getenv("NOTION_PROPERTY_DATE", "날짜")
MEMO_PROP = os.getenv("NOTION_PROPERTY_MEMO", "간단메모")
TAGS_PROP = os.getenv("NOTION_PROPERTY_TAGS", "태그")
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "50")) * 1024 * 1024
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


def _is_raster_media_block(block: dict) -> bool:
    kind = block.get("type")
    if kind == "image":
        return True
    if kind not in {"file", "pdf"}:
        return False
    data = block.get(kind) or {}
    media = data.get("file") or data.get("external") or {}
    mime = str(media.get("mime_type", "")).lower()
    url = str(media.get("url", "")).lower().split("?", 1)[0]
    name = str(data.get("name", "")).lower().split("?", 1)[0]
    image_suffixes = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
    return (
        mime.startswith("image/")
        or url.endswith(image_suffixes)
        or name.endswith(image_suffixes)
    )


def ordered_visual_events(blocks: list[dict]):
    """Yield original image and code blocks in document order."""
    def walk(items: list[dict]):
        for block in items or []:
            kind = block.get("type", "")
            if _is_raster_media_block(block):
                yield "image", block
            elif kind == "code":
                yield "code", block
            yield from walk(block.get("_children") or [])

    yield from walk(blocks)


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
        # Some Notion CDN URLs have no filename suffix; fall back to the
        # original attachment name before guessing from the MIME type.
        name_extension = Path(unquote(str(data.get("name") or ""))).suffix.lower()
        if name_extension in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf"):
            extension = name_extension
        else:
            extension = mimetypes.guess_extension(media.get("mime_type", "")) or ".bin"

    destination = folder / f"이미지_{index:02d}_{safe_name(Path(original_name).stem)}{extension}"
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



def create_image_bundle(
    draft_folder: Path,
    image_manifest: list[dict[str, str | int]],
) -> Path | None:
    """Bundle all generated images into one ordered ZIP for easy bulk upload."""
    archive_path = draft_folder / "07_이미지_전체.zip"
    available = [
        item for item in sorted(image_manifest, key=lambda x: int(x.get("number", 0)))
        if (draft_folder / str(item.get("path", ""))).is_file()
    ]
    if not available:
        archive_path.unlink(missing_ok=True)
        return None

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        readme_lines = [
            "SSS Robotics 네이버 블로그 이미지 모음",
            "압축을 푼 뒤 파일명 앞의 번호 순서대로 네이버 편집기에 첨부하세요.",
            "",
        ]
        for item in available:
            source = draft_folder / str(item["path"])
            archive.write(source, arcname=source.name)
            readme_lines.append(
                f"{int(item.get('number', 0)):02d}. {item.get('label', source.name)} — {source.name}"
            )
        archive.writestr("이미지_업로드_순서.txt", "\n".join(readme_lines) + "\n")
    return archive_path


def build_post_body_text(body_text: str) -> str:
    """Normalize the article body without appending database-property tags."""
    body = (body_text or "").strip()
    return body + "\n" if body else ""

DATE_SCOPE_OPTIONS = ("Today", "Yesterday", "Week", "Month", "ALL")
FETCH_MODE_OPTIONS = (*DATE_SCOPE_OPTIONS, "EDITED_WEEK")
EDITED_LOOKBACK_DAYS = 14


def requested_fetch_mode(environ: dict[str, str] | None = None) -> str:
    """Read FETCH_MODE first, retaining NAVER_DATE_SCOPE as a legacy fallback."""
    source = os.environ if environ is None else environ
    return normalize_fetch_mode(
        source.get("FETCH_MODE") or source.get("NAVER_DATE_SCOPE") or "Today"
    )


def normalize_fetch_mode(value: str | None) -> str:
    """Normalize the workflow's FETCH_MODE and legacy local env variable."""
    raw = (value or "Today").strip()
    normalized = raw.lower()
    modes = {mode.lower(): mode for mode in FETCH_MODE_OPTIONS}
    if normalized not in modes:
        raise ValueError(
            f"지원하지 않는 조회 범위입니다: {raw!r}. "
            f"사용 가능 값: {', '.join(FETCH_MODE_OPTIONS)}"
        )
    return modes[normalized]


def notion_datetime(value: str | None) -> datetime | None:
    """Parse Notion's ISO-8601 timestamp into an aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def draft_outputs_exist(folder: Path) -> bool:
    """Return whether a tracked draft folder contains its core generated files."""
    required_files = (
        "01_네이버_제목.txt",
        "02_네이버_본문.txt",
        "02_네이버_본문_서식.docx",
        "metadata.json",
    )
    return folder.is_dir() and all((folder / name).is_file() for name in required_files)


def _stable_blocks_for_fingerprint(blocks: list[dict]) -> list[dict]:
    """Normalize block trees while dropping expiring Notion-hosted file URLs."""
    normalized: list[dict] = []
    for block in blocks or []:
        item = {key: value for key, value in block.items() if key != "_children"}
        kind = str(block.get("type") or "")
        data = item.get(kind)
        if isinstance(data, dict):
            data = dict(data)
            hosted_file = data.get("file")
            if isinstance(hosted_file, dict):
                hosted_file = dict(hosted_file)
                # Notion-hosted file URLs are temporary signed URLs and may
                # rotate between requests without the article itself changing.
                hosted_file.pop("url", None)
                hosted_file.pop("expiry_time", None)
                data["file"] = hosted_file
            item[kind] = data
        item["_children"] = _stable_blocks_for_fingerprint(block.get("_children") or [])
        normalized.append(item)
    return normalized


def page_content_fingerprint(page: dict, blocks: list[dict]) -> str:
    """Hash the Notion page properties and complete nested block tree.

    Including block content catches article edits even if the parent page's
    last_edited_time is unchanged. Temporary signed Notion file URLs are ignored.
    """
    payload = {
        "last_edited_time": page.get("last_edited_time", ""),
        "title": title_of(page),
        "date": date_of(page).isoformat() if date_of(page) else None,
        "memo": memo_of(page),
        "tags": tags_of(page),
        "blocks": _stable_blocks_for_fingerprint(blocks),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def should_skip_processed_page(
    previous: dict | None,
    last_edited: str,
    content_hash: str,
    draft_exists: bool = True,
) -> bool:
    """Skip only published pages or unchanged drafts whose outputs still exist.

    Every fetch mode automatically refreshes an existing draft when its page
    timestamp or content fingerprint changes. Missing/incomplete output files
    are regenerated even when the processing history still contains the page.
    Published pages remain protected in every mode.
    """
    if not previous:
        return False
    if previous.get("status") == "published":
        return True
    if not draft_exists:
        return False
    previous_last_edited = previous.get("last_edited_time") or ""
    previous_hash = previous.get("content_hash") or ""
    return (
        bool(previous_last_edited)
        and previous_last_edited == (last_edited or "")
        and bool(previous_hash)
        and previous_hash == content_hash
    )


def date_window(
    today: date, scope: str = "Today"
) -> tuple[date | None, date | None]:
    """Return an inclusive KST calendar-date window for the selected scope.

    Today: today only
    Yesterday: yesterday only
    Week: the latest 7 calendar days, including today
    Month: the first day of this month through today
    ALL: no date boundary
    """
    normalized = (scope or "Today").strip().lower()
    if normalized == "today":
        return today, today
    if normalized == "yesterday":
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    if normalized == "week":
        return today - timedelta(days=6), today
    if normalized == "month":
        return today.replace(day=1), today
    if normalized == "all":
        return None, None
    raise ValueError(
        f"지원하지 않는 조회 범위입니다: {scope!r}. "
        f"사용 가능 값: {', '.join(DATE_SCOPE_OPTIONS)}"
    )


def main() -> None:
    DRAFTS.mkdir(parents=True, exist_ok=True)
    if TRACKER.exists():
        tracker = json.loads(TRACKER.read_text(encoding="utf-8"))
    else:
        tracker = {"version": 1, "pages": {}}

    processed = tracker.setdefault("pages", {})
    today_kst = datetime.now(KST).date()
    date_scope = requested_fetch_mode()
    edited_after = None

    if date_scope == "EDITED_WEEK":
        edited_after_dt = datetime.now(timezone.utc) - timedelta(days=EDITED_LOOKBACK_DAYS)
        edited_after = edited_after_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        start_date, end_date = None, None
        pages = query_database(last_edited_after=edited_after)
    else:
        start_date, end_date = date_window(today_kst, date_scope)
        # API 단계에서 가능한 한 범위를 좁혀 조회합니다. ALL인 경우에만 전체 페이지를 조회합니다.
        if start_date is None and end_date is None:
            pages = query_database()
        else:
            pages = query_database(
                date_property=DATE_PROP,
                start_date=start_date.isoformat() if start_date else None,
                end_date=end_date.isoformat() if end_date else None,
            )
    candidates = []

    # API 결과를 한 번 더 확인해 범위 밖 페이지가 섞이지 않도록 합니다.
    for page in pages:
        page_date = date_of(page)
        if page_date is None:
            print(f"[SKIP] 날짜 속성이 없거나 올바르지 않은 페이지: {title_of(page)}")
            continue
        if date_scope == "EDITED_WEEK":
            edited_at = notion_datetime(page.get("last_edited_time"))
            if edited_at is None or edited_after is None:
                continue
            if edited_at < datetime.fromisoformat(edited_after.replace("Z", "+00:00")):
                continue
        else:
            if start_date is not None and page_date < start_date:
                continue
            if end_date is not None and page_date > end_date:
                continue
        candidates.append((page_date, page))

    candidates.sort(key=lambda item: (item[0], title_of(item[1])))
    if date_scope == "EDITED_WEEK":
        scope_description = f"최근 {EDITED_LOOKBACK_DAYS}일 수정된 페이지 (last_edited_time ≥ {edited_after})"
    elif start_date is None and end_date is None:
        scope_description = "전체 기간"
    elif start_date == end_date:
        scope_description = start_date.isoformat()
    else:
        scope_description = (
            f"{start_date.isoformat() if start_date else '시작 제한 없음'} ~ "
            f"{end_date.isoformat() if end_date else '종료 제한 없음'}"
        )
    print(
        f"[INFO] 한국시간 조회 범위 {date_scope} ({scope_description}) 대상: "
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

        previous_folder_name = (previous or {}).get("folder") or ""
        previous_folder = DRAFTS / previous_folder_name if previous_folder_name else None
        outputs_exist = bool(previous_folder and draft_outputs_exist(previous_folder))

        # Load the current Notion blocks before deciding to skip. This makes
        # edits to the article body detectable, not just page-property edits.
        blocks = load_tree(get_blocks(page_id))
        content_hash = page_content_fingerprint(page, blocks)

        if should_skip_processed_page(previous, last_edited, content_hash, outputs_exist):
            print(f"[SKIP] 변경 없음: {title_of(page)} (기존 초안 유지)")
            skipped += 1
            continue

        if previous:
            if not outputs_exist:
                print(f"[REBUILD] 기존 초안 파일이 없거나 불완전함: {title_of(page)}")
            elif (previous.get("last_edited_time") != last_edited
                  or previous.get("content_hash") != content_hash):
                print(f"[UPDATE] Notion 본문/속성 변경 감지: {title_of(page)}")
            else:
                print(f"[REBUILD] 기존 초안에 내용 해시가 없어 최신 형식으로 갱신: {title_of(page)}")

        title = title_of(page)
        memo = memo_of(page)
        tags = tags_of(page)
        folder_name = safe_name(f"{page_date.isoformat()}_{title}")
        folder = DRAFTS / folder_name

        if folder.exists() and (not previous or previous.get("folder") != folder_name):
            folder_name = f"{folder_name}_{page_id.replace('-', '')[:6]}"
            folder = DRAFTS / folder_name

        images_folder = folder / "images"
        images_folder.mkdir(parents=True, exist_ok=True)
        # Remove only generated image assets from prior runs; preserve any unrelated files.
        for old_file in images_folder.iterdir():
            if old_file.is_file() and (
                old_file.name.startswith("이미지_")
                or old_file.name.startswith("code_")
                or old_file.name.startswith("image_")  # legacy unnumbered photo assets
                or re.match(r"^\d{2}_", old_file.name)
            ):
                old_file.unlink()

        image_map: dict[str, str] = {}
        image_manifest: list[dict[str, str | int]] = []
        code_sources: list[dict[str, str]] = []
        asset_number = 0

        for event_type, block in ordered_visual_events(blocks):
            data = block.get(block.get("type"), {}) or {}
            if event_type == "image":
                candidate_number = asset_number + 1
                saved = save_media(block, images_folder, candidate_number)
                if not saved:
                    continue
                asset_number = candidate_number
                relative_path = f"images/{saved.name}"
                if block.get("id"):
                    image_map[block["id"]] = relative_path
                caption = plain(data.get("caption") or []).strip() if isinstance(data.get("caption"), list) else ""
                image_manifest.append({
                    "number": asset_number,
                    "path": relative_path,
                    "label": caption or data.get("name") or "본문 이미지",
                    "kind": "photo",
                })

            elif event_type == "code":
                code = plain(data.get("rich_text") or [])
                if not code.strip():
                    continue
                asset_number += 1
                language = str(data.get("language") or "text")
                digest_name = code_image_filename(code, language)
                stem = Path(digest_name).stem
                filename = f"이미지_{asset_number:02d}_{safe_name(stem)}.png"
                destination = images_folder / filename
                render_code_image(code, language, destination)
                relative_path = f"images/{filename}"
                if block.get("id"):
                    image_map[block["id"]] = relative_path
                code_sources.append({"language": language, "code": code, "filename": filename})
                image_manifest.append({
                    "number": asset_number,
                    "path": relative_path,
                    "label": f"{language.upper()} 코드 이미지",
                    "kind": "code",
                })

        folder.mkdir(parents=True, exist_ok=True)
        image_bundle = create_image_bundle(folder, image_manifest)
        text_lines = []
        headings = []
        body_html = render_blocks(
            blocks, image_map, text_lines,
            code_image_dir=images_folder,
            headings=headings,
        )

        # TXT is the plain-text fallback. DOCX carries exact heading sizes and yellow image markers.
        body_text = render_plain_text(
            blocks, image_map, headings, code_sources
        )
        (folder / "01_네이버_제목.txt").write_text(title.strip() + "\n", encoding="utf-8")

        final_body_text = build_post_body_text(body_text)
        (folder / "02_네이버_본문.txt").write_text(final_body_text, encoding="utf-8")
        write_naver_docx(
            final_body_text,
            folder / "02_네이버_본문_서식.docx",
            headings=headings,
        )

        image_list_lines = [
            "네이버 블로그 이미지 업로드 목록",
            "",
            "본문의 🟨 이미지 NN 삽입 위치 표시를 위에서부터 순서대로 확인하세요.",
            "파일명에도 같은 번호가 붙어 있어 순서대로 선택하면 됩니다.",
            "모든 이미지는 07_이미지_전체.zip에도 한 번에 묶여 있습니다.",
            "",
        ]
        image_list_lines.extend(
            f"{int(item['number']):02d}. {item['label']} — {item['path']}"
            for item in image_manifest
        )
        if not image_manifest:
            image_list_lines.append("업로드할 이미지가 없습니다.")
        (folder / "03_이미지_목록.txt").write_text(
            "\n".join(image_list_lines) + "\n", encoding="utf-8"
        )

        # Keep original code in a separate optional file. The main article
        # points to its PNG at the exact insertion location.
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
            "[ ] 02_네이버_본문_서식.docx를 Word에서 열어 본문을 복사해 네이버 편집기에 붙여넣기",
            "[ ] 노란색 이미지 삽입 안내 문구를 지우고 해당 이미지 첨부",
            "[ ] 07_이미지_전체.zip을 내려받아 압축을 풀고, 이미지 01부터 마지막 번호까지 첨부",
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
            "네이버 편집기에 서식을 적용하려면 DOCX 파일을 기본 원고로 사용합니다.",
            "TXT는 서식이 필요 없을 때 사용하는 일반 텍스트 대체본입니다.",
            "HTML 파일은 미리보기 전용이며, HTML에서 본문을 복사하지 마세요.",
            "",
            "1. GitHub에서 해당 초안 폴더 전체를 다운로드하고 압축을 풉니다.",
            "2. 01_네이버_제목.txt를 열어 제목 한 줄을 복사한 뒤 네이버 제목란에 붙여넣습니다.",
            "3. 서식을 유지해 복사하려면 02_네이버_본문_서식.docx를 Word에서 엽니다.",
            "4. DOCX에서 본문 전체를 복사해 네이버 편집기에 붙여넣습니다. 제목 크기는 # 30pt, ## 28pt, ### 24pt, #### 19pt, 일반 본문은 15pt로 지정되어 있습니다.",
            "5. 노란색으로 표시된 🟨 이미지 NN 삽입 위치 문구를 확인하고, 문구를 지운 뒤 해당 이미지를 첨부합니다.",
            "6. 사진을 한꺼번에 준비하려면 07_이미지_전체.zip을 내려받아 압축을 풉니다.",
            "7. 압축을 푼 이미지 파일을 네이버 사진 첨부 창에서 여러 장 선택할 수 있습니다.",
            "8. 본문 배치 순서는 파일명 앞 번호와 03_이미지_목록.txt를 기준으로 맞춥니다.",
            "9. TXT만 필요하면 02_네이버_본문.txt를 사용하세요. TXT에는 글자 크기와 노란색 배경이 저장되지 않습니다.",
            "10. YouTube·홈페이지·이메일 주소가 본문에 있는지 확인합니다.",
            "11. 최종 확인 후 직접 발행합니다.",
            "",
            "주의: 로컬 이미지 파일은 본문 복사만으로 업로드되지 않습니다. 07_이미지_전체.zip에서 추출한 파일을 네이버 사진 첨부 기능으로 등록해야 합니다.",
            "06_미리보기.html은 브라우저 확인용입니다. 서식이 필요한 원고는 DOCX, 순수 텍스트는 TXT를 사용하세요.",
        ]
        (folder / "00_네이버_블로그_사용방법.txt").write_text(
            "\n".join(usage_guide) + "\n", encoding="utf-8"
        )

        (folder / "06_미리보기.html").write_text(
            wrap_html(title, body_html, [], headings), encoding="utf-8"
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
            "content_hash": content_hash,
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
            "content_hash": content_hash,
            "status": "draft",
            "title": title,
            "date": page_date.isoformat(),
        }
        created += 1
        print(f"[DRAFT] {folder_name}")

    tracker["last_scan"] = datetime.now(timezone.utc).isoformat()
    tracker["scan_window"] = {
        "scope": date_scope,
        "start": start_date.isoformat() if start_date else None,
        "end": end_date.isoformat() if end_date else None,
        "last_edited_after": edited_after,
        "lookback_days": EDITED_LOOKBACK_DAYS if date_scope == "EDITED_WEEK" else None,
        "timezone": "Asia/Seoul",
    }
    TRACKER.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] 신규/갱신 초안 {created}개, 건너뜀 {skipped}개")


if __name__ == "__main__":
    main()

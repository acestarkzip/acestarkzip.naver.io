from __future__ import annotations

import os
import time
import requests
from typing import Any

API = "https://api.notion.com/v1"
TOKEN = os.getenv("NOTION_TOKEN", "").strip()
DB = os.getenv("NOTION_DATABASE_ID", "").strip()
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": os.getenv("NOTION_VERSION", "2022-06-28"),
}
_last_request = 0.0


def request(method: str, path: str, **kwargs: Any) -> dict:
    """Notion API request with light rate limiting and retry handling."""
    global _last_request

    if not TOKEN:
        raise RuntimeError("NOTION_TOKEN이 설정되지 않았습니다.")

    for attempt in range(5):
        wait = 0.35 - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)

        response = requests.request(
            method,
            API + path,
            headers=HEADERS,
            timeout=40,
            **kwargs,
        )
        _last_request = time.monotonic()

        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 4:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            time.sleep(float(retry_after) if retry_after else min(2 ** attempt, 15))
            continue

        if not response.ok:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:1000]
            raise RuntimeError(
                f"Notion API 오류 ({response.status_code}): {detail}"
            )

        return response.json()

    raise RuntimeError("Notion API 요청 재시도 횟수를 초과했습니다.")


def query_database(
    date_property: str | None = None,
    on_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    last_edited_after: str | None = None,
) -> list[dict]:
    """Query all pages or filter by a Notion date property / page edit time.

    - on_date: exact calendar date
    - start_date / end_date: inclusive date range; either boundary may be omitted
    - last_edited_after: ISO-8601 timestamp; only recently edited pages are returned
    - no filters: all pages (ALL manual scope)
    """
    if not DB:
        raise RuntimeError("NOTION_DATABASE_ID가 설정되지 않았습니다.")

    if on_date and (start_date is not None or end_date is not None):
        raise ValueError("on_date와 날짜 범위(start_date/end_date)는 함께 사용할 수 없습니다.")

    has_date_filter = on_date is not None or start_date is not None or end_date is not None
    if has_date_filter and not date_property:
        raise ValueError("날짜 필터를 사용하려면 date_property가 필요합니다.")
    if last_edited_after is not None and has_date_filter:
        raise ValueError("last_edited_after와 게시일 날짜 필터는 함께 사용할 수 없습니다.")

    date_filter: dict[str, str] = {}
    if on_date is not None:
        date_filter["equals"] = on_date
    elif start_date is not None and end_date is not None and start_date == end_date:
        date_filter["equals"] = start_date
    else:
        if start_date is not None:
            date_filter["on_or_after"] = start_date
        if end_date is not None:
            date_filter["on_or_before"] = end_date

    results: list[dict] = []
    cursor = None

    while True:
        payload: dict[str, Any] = {"page_size": 100}
        if date_filter:
            payload["filter"] = {
                "property": date_property,
                "date": date_filter,
            }
        elif last_edited_after is not None:
            # Notion supports a page-level timestamp filter without adding a
            # dedicated "Last edited time" property to the database.
            payload["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {"on_or_after": last_edited_after},
            }
        if cursor:
            payload["start_cursor"] = cursor

        data = request("POST", f"/databases/{DB}/query", json=payload)
        results.extend(data.get("results", []))

        if not data.get("has_more") or not data.get("next_cursor"):
            break
        cursor = data["next_cursor"]

    return results


def get_blocks(block_id: str) -> list[dict]:
    """Return all children of a block/page, following pagination."""
    results: list[dict] = []
    cursor = None

    while True:
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor

        data = request("GET", f"/blocks/{block_id}/children", params=params)
        results.extend(data.get("results", []))

        if not data.get("has_more") or not data.get("next_cursor"):
            break
        cursor = data["next_cursor"]

    return results


def load_tree(blocks: list[dict], depth: int = 0, max_depth: int = 20) -> list[dict]:
    """
    Recursively attach child blocks as `_children`.
    The depth limit prevents runaway recursion in deeply nested pages.
    """
    if depth >= max_depth:
        for block in blocks:
            block["_children"] = []
        return blocks

    for block in blocks:
        if block.get("has_children"):
            try:
                children = get_blocks(block["id"])
                block["_children"] = load_tree(children, depth + 1, max_depth)
            except Exception as exc:
                block["_children"] = []
                print(
                    f"[WARN] 하위 블록을 읽지 못했습니다 "
                    f"({block.get('type')} / {block.get('id')}): {exc}"
                )
        else:
            block["_children"] = []

    return blocks

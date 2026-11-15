from __future__ import annotations

import html
from typing import Any


def plain(items: list[dict] | None) -> str:
    """Extract plain text from Notion rich_text/title arrays safely."""
    parts: list[str] = []
    for item in items or []:
        item = item or {}
        text_obj = item.get("text") or {}
        parts.append(item.get("plain_text") or text_obj.get("content") or "")
    return "".join(parts)


def _safe_link(item: dict) -> str | None:
    """Notion's text.link can be null; normalize all link shapes safely."""
    item = item or {}
    text_obj = item.get("text") or {}
    link_obj = text_obj.get("link") or {}
    href = item.get("href") or link_obj.get("url")
    return href if isinstance(href, str) and href.strip() else None


def _rich_text_item(item: dict) -> str:
    item = item or {}
    text_obj = item.get("text") or {}
    raw = item.get("plain_text") or text_obj.get("content") or ""
    value = html.escape(raw, quote=False)
    annotations = item.get("annotations") or {}

    # Apply inline marks from inside out. HTML nesting remains valid.
    if annotations.get("code"):
        value = f"<code>{value}</code>"
    if annotations.get("bold"):
        value = f"<strong>{value}</strong>"
    if annotations.get("italic"):
        value = f"<em>{value}</em>"
    if annotations.get("strikethrough"):
        value = f"<del>{value}</del>"
    if annotations.get("underline"):
        value = f"<u>{value}</u>"

    color = annotations.get("color")
    color_map = {
        "gray": "#777777", "brown": "#9a6b4f", "orange": "#d97706",
        "yellow": "#b58900", "green": "#16803c", "blue": "#2563eb",
        "purple": "#7c3aed", "pink": "#db2777", "red": "#dc2626",
    }
    if isinstance(color, str) and color != "default":
        normalized = color.removesuffix("_background")
        if normalized in color_map:
            value = f'<span style="color:{color_map[normalized]}">{value}</span>'

    href = _safe_link(item)
    if href:
        value = f'<a href="{html.escape(href, quote=True)}">{value}</a>'
    return value


def rich_text(items: list[dict] | None) -> str:
    return "".join(_rich_text_item(item) for item in (items or []))


def _text_fields(data: dict) -> tuple[str, str]:
    rich = data.get("rich_text") or data.get("title") or []
    return rich_text(rich), plain(rich).strip()


def _render_list(
    blocks: list[dict], start: int, kind: str,
    image_map: dict[str, str], lines: list[str], depth: int,
) -> tuple[str, int]:
    tag = "ul" if kind == "bulleted_list_item" else "ol"
    items: list[str] = []
    index = start
    while index < len(blocks) and blocks[index].get("type") == kind:
        block = blocks[index]
        data = block.get(kind) or {}
        item_html, raw = _text_fields(data)
        nested = render_blocks(block.get("_children") or [], image_map, lines, depth + 1)
        items.append(f"<li>{item_html}{nested}</li>")
        lines.append(("• " if tag == "ul" else f"{len(items)}. ") + raw)
        index += 1
    return f"<{tag}>{''.join(items)}</{tag}>", index


def _render_table(block: dict, lines: list[str]) -> str:
    rows = block.get("_children") or []
    table_data = block.get("table") or {}
    has_column_header = bool(table_data.get("has_column_header"))
    has_row_header = bool(table_data.get("has_row_header"))
    output: list[str] = []
    for row_index, row in enumerate(rows):
        cells = (row.get("table_row") or {}).get("cells") or []
        rendered: list[str] = []
        plain_cells: list[str] = []
        for col_index, cell in enumerate(cells):
            tag = "th" if (has_column_header and row_index == 0) or (has_row_header and col_index == 0) else "td"
            rendered.append(f"<{tag}>{rich_text(cell)}</{tag}>")
            plain_cells.append(plain(cell))
        output.append("<tr>" + "".join(rendered) + "</tr>")
        lines.append(" | ".join(plain_cells))
    if not output:
        return ""
    return '<div class="table-wrap"><table><tbody>' + "".join(output) + "</tbody></table></div>"


def _media_url(data: dict) -> str:
    media = data.get("file") or data.get("external") or {}
    if not isinstance(media, dict):
        media = {}
    return media.get("url") or data.get("url") or ""


def render_blocks(
    blocks: list[dict], image_map: dict[str, str] | None,
    lines: list[str], depth: int = 0,
) -> str:
    """Convert Notion block trees to semantic HTML while retaining source text."""
    if depth > 20:
        return '<p class="conversion-warning">[중첩 블록 깊이 제한]</p>'

    image_map = image_map or {}
    output: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index] or {}
        kind = block.get("type", "")
        data = block.get(kind) or {}
        children = block.get("_children") or []
        value, raw = _text_fields(data)

        if kind in ("bulleted_list_item", "numbered_list_item"):
            rendered, index = _render_list(blocks, index, kind, image_map, lines, depth)
            output.append(rendered)
            continue
        elif kind == "paragraph":
            if value:
                output.append(f"<p>{value}</p>")
                lines.append(raw)
        elif kind in ("heading_1", "heading_2", "heading_3"):
            level = int(kind[-1])
            output.append(f"<h{level}>{value}</h{level}>")
            lines.extend(["", raw, ""])
        elif kind == "to_do":
            mark = "☑" if data.get("checked") else "☐"
            output.append(f'<p class="todo">{mark} {value}</p>')
            lines.append(f"{mark} {raw}")
        elif kind == "quote":
            output.append(f"<blockquote>{value}</blockquote>")
            lines.append("> " + raw)
        elif kind == "callout":
            icon = data.get("icon") or {}
            icon_text = icon.get("emoji") or "💡"
            output.append(f'<aside class="callout"><span>{html.escape(icon_text)}</span> {value}</aside>')
            lines.append(f"{icon_text} {raw}")
        elif kind == "code":
            code = plain(data.get("rich_text") or [])
            language = html.escape(data.get("language") or "plain text", quote=True)
            output.append(f'<pre data-language="{language}"><code>{html.escape(code)}</code></pre>')
            lines.extend(["", code, ""])
        elif kind == "divider":
            output.append("<hr>")
            lines.append("──────────")
        elif kind == "image":
            src = image_map.get(block.get("id"))
            caption_items = data.get("caption") or []
            caption = rich_text(caption_items)
            alt = html.escape(plain(caption_items), quote=True)
            if src:
                output.append(f'<figure><img src="{html.escape(src, quote=True)}" alt="{alt}"><figcaption>{caption}</figcaption></figure>')
            else:
                source_url = _media_url(data)
                if source_url:
                    output.append(f'<figure><img src="{html.escape(source_url, quote=True)}" alt="{alt}"><figcaption>{caption}</figcaption></figure>')
                else:
                    output.append('<p class="conversion-warning">[이미지 다운로드 실패]</p>')
            lines.append("[이미지: " + (plain(caption_items) or "설명 없음") + "]")
        elif kind in ("file", "pdf", "video", "audio", "bookmark", "embed", "link_preview"):
            url = _media_url(data) or data.get("url", "")
            label = data.get("name") or data.get("title") or kind
            if url:
                output.append(f'<p class="attachment"><a href="{html.escape(url, quote=True)}">{html.escape(str(label))}</a></p>')
                lines.append(f"[{kind}] {url}")
        elif kind == "table":
            output.append(_render_table(block, lines))
        elif kind == "equation":
            expression = data.get("expression", "")
            output.append(f'<p class="equation"><code>{html.escape(expression)}</code></p>')
            lines.append(expression)
        elif kind == "toggle":
            summary = value or "자세히 보기"
            nested = render_blocks(children, image_map, lines, depth + 1)
            output.append(f"<details><summary>{summary}</summary>{nested}</details>")
            if raw:
                lines.append(raw)
        elif kind in ("column_list", "column", "synced_block", "template"):
            nested = render_blocks(children, image_map, lines, depth + 1)
            if nested:
                output.append(f'<div class="notion-{kind}">{nested}</div>')
        elif kind in ("child_page", "child_database"):
            label = data.get("title", "하위 페이지")
            output.append(f"<p><strong>{html.escape(label)}</strong></p>")
            lines.append(label)
        elif value:
            output.append(f"<p>{value}</p>")
            lines.append(raw)

        # Containers above already rendered their children. Other blocks may still have children.
        if children and kind not in ("table", "toggle", "column_list", "column", "synced_block", "template", "bulleted_list_item", "numbered_list_item"):
            nested = render_blocks(children, image_map, lines, depth + 1)
            if nested:
                output.append(nested)
        index += 1

    return "\n".join(part for part in output if part)


def wrap_html(title: str, memo: str, body: str, tags: list[str]) -> str:
    """Create a standalone browser preview. This is not a Naver editor import file."""
    tag_html = " ".join("#" + html.escape(tag) for tag in tags)
    safe_title = html.escape(title)
    safe_memo = html.escape(memo)
    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
body {{ font-family: Arial, 'Malgun Gothic', sans-serif; line-height: 1.8; max-width: 820px; margin: 32px auto; padding: 0 20px; color: #222; }}
img {{ max-width: 100%; height: auto; }}
pre {{ overflow-x: auto; white-space: pre-wrap; background: #f3f3f3; padding: 14px; border-radius: 6px; }}
code {{ font-family: Consolas, monospace; }}
blockquote {{ border-left: 4px solid #888; padding: 8px 16px; margin-left: 0; background: #f7f7f7; }}
.callout {{ padding: 12px 16px; background: #f4f7fb; border-radius: 6px; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
figcaption {{ color: #666; font-size: .9em; }}
.todo {{ list-style: none; }}
.conversion-warning {{ color: #b45309; }}
a {{ overflow-wrap: anywhere; }}
</style>
</head>
<body>
<header><h1>{safe_title}</h1>{f'<p>{safe_memo}</p>' if memo else ''}{f'<p class="tags">{tag_html}</p>' if tag_html else ''}</header>
<main>
{body}
</main>
</body>
</html>
'''

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote


def plain(items: list[dict] | None) -> str:
    """Extract readable text from Notion rich_text/title arrays."""
    parts = []
    for item in items or []:
        text_obj = item.get("text") or {}
        parts.append(item.get("plain_text") or text_obj.get("content", ""))
    return "".join(parts)


def _rich_text_item(item: dict) -> str:
    value = html.escape(
        item.get("plain_text")
        or (item.get("text") or {}).get("content", ""),
        quote=False,
    )
    annotations = item.get("annotations") or {}

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
    if color and color != "default":
        # Keep Notion's color hint without allowing arbitrary CSS input.
        safe_color = color.replace("_background", "").replace("_", "")
        color_map = {
            "gray": "#777777", "brown": "#9a6b4f", "orange": "#d97706",
            "yellow": "#b58900", "green": "#16803c", "blue": "#2563eb",
            "purple": "#7c3aed", "pink": "#db2777", "red": "#dc2626",
        }
        if safe_color in color_map:
            value = f'<span style="color:{color_map[safe_color]}">{value}</span>'

    href = item.get("href") or (item.get("text") or {}).get("link", {}).get("url")
    if href:
        value = f'<a href="{html.escape(href, quote=True)}">{value}</a>'

    return value


def rich_text(items: list[dict] | None) -> str:
    return "".join(_rich_text_item(item) for item in (items or []))


def _block_text(data: dict) -> tuple[str, str]:
    rich = data.get("rich_text") or data.get("title") or []
    return rich_text(rich), plain(rich).strip()


def _render_list(blocks: list[dict], start: int, kind: str, lines: list[str], depth: int) -> tuple[str, int]:
    tag = "ul" if kind == "bulleted_list_item" else "ol"
    rendered = []
    index = start
    while index < len(blocks) and blocks[index].get("type") == kind:
        block = blocks[index]
        data = block.get(kind) or {}
        value, raw = _block_text(data)
        child_html = render_blocks(block.get("_children") or [], {}, lines, depth + 1)
        rendered.append(f"<li>{value}{child_html}</li>")
        lines.append(("• " if tag == "ul" else f"{len(rendered)}. ") + raw)
        index += 1
    return f"<{tag}>{''.join(rendered)}</{tag}>", index


def _render_table(block: dict, lines: list[str], depth: int) -> str:
    rows = block.get("_children") or []
    if not rows:
        return ""

    table_data = block.get("table") or {}
    has_header = bool(table_data.get("has_column_header"))
    html_rows = []
    for row_index, row in enumerate(rows):
        cells = (row.get("table_row") or {}).get("cells") or []
        rendered_cells = []
        for cell in cells:
            cell_html = rich_text(cell)
            rendered_cells.append(cell_html)
        tag = "th" if has_header and row_index == 0 else "td"
        html_rows.append(
            "<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in rendered_cells) + "</tr>"
        )
        lines.append(" | ".join(plain(cell) for cell in cells))

    return "<table><tbody>" + "".join(html_rows) + "</tbody></table>"


def _media_url(data: dict) -> str:
    media = data.get("file") or data.get("external") or {}
    return media.get("url") or data.get("url") or ""


def render_blocks(
    blocks: list[dict],
    image_map: dict[str, str] | None,
    lines: list[str],
    depth: int = 0,
) -> str:
    """
    Convert Notion blocks into semantic HTML.
    `image_map` maps Notion image block IDs to downloaded relative file paths.
    The function signature remains compatible with exporter.py.
    """
    if depth > 20:
        return ""

    image_map = image_map or {}
    output: list[str] = []
    index = 0

    while index < len(blocks):
        block = blocks[index]
        kind = block.get("type", "")
        data = block.get(kind) or {}
        children = block.get("_children") or []
        value, raw = _block_text(data)

        if kind in ("bulleted_list_item", "numbered_list_item"):
            rendered, index = _render_list(blocks, index, kind, lines, depth)
            output.append(rendered)
            continue

        if kind == "paragraph":
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
            output.append(
                f'<pre data-language="{language}"><code>{html.escape(code)}</code></pre>'
            )
            lines.extend(["", code, ""])

        elif kind == "divider":
            output.append("<hr>")
            lines.append("──────────")

        elif kind == "image":
            src = image_map.get(block.get("id"))
            caption = rich_text(data.get("caption") or [])
            if src:
                output.append(
                    f'<figure><img src="{html.escape(src, quote=True)}" '
                    f'alt="{html.escape(plain(data.get("caption") or []), quote=True)}">'
                    f'<figcaption>{caption}</figcaption></figure>'
                )
            else:
                source_url = _media_url(data)
                if source_url:
                    output.append(
                        f'<figure><img src="{html.escape(source_url, quote=True)}">'
                        f'<figcaption>{caption}</figcaption></figure>'
                    )
                else:
                    output.append('<p class="conversion-warning">[이미지 다운로드 실패]</p>')
            lines.append("[이미지: " + (plain(data.get("caption") or []) or "설명 없음") + "]")

        elif kind in ("file", "pdf", "video", "audio", "bookmark", "embed"):
            url = _media_url(data)
            label = data.get("name") or data.get("title") or kind
            if url:
                output.append(
                    f'<p class="attachment"><a href="{html.escape(url, quote=True)}">'
                    f'{html.escape(str(label))}</a></p>'
                )
                lines.append(f"[{kind}] {url}")

        elif kind == "table":
            output.append(_render_table(block, lines, depth))

        elif kind == "table_row":
            # Rows are rendered by their parent table.
            pass

        elif kind == "equation":
            expression = data.get("expression", "")
            output.append(f"<p class=\"equation\"><code>{html.escape(expression)}</code></p>")
            lines.append(expression)

        elif kind == "toggle":
            summary = value or "자세히 보기"
            child_html = render_blocks(children, image_map, lines, depth + 1)
            output.append(f"<details><summary>{summary}</summary>{child_html}</details>")
            if raw:
                lines.append(raw)

        elif kind == "child_page":
            label = data.get("title", "하위 페이지")
            output.append(f"<p><strong>{html.escape(label)}</strong></p>")
            lines.append(label)

        elif kind == "child_database":
            label = data.get("title", "하위 데이터베이스")
            output.append(f"<p><strong>{html.escape(label)}</strong></p>")
            lines.append(label)

        elif kind in ("column_list", "column", "synced_block", "template"):
            child_html = render_blocks(children, image_map, lines, depth + 1)
            if child_html:
                output.append(f'<div class="notion-{kind}">{child_html}</div>')

        elif kind == "link_preview":
            url = data.get("url", "")
            if url:
                output.append(f'<p><a href="{html.escape(url, quote=True)}">{html.escape(url)}</a></p>')
                lines.append(url)

        elif value:
            output.append(f"<p>{value}</p>")
            lines.append(raw)

        # Render children for supported generic/container blocks only.
        if children and kind not in (
            "table", "toggle", "column_list", "column", "synced_block", "template",
            "bulleted_list_item", "numbered_list_item",
        ):
            child_html = render_blocks(children, image_map, lines, depth + 1)
            if child_html:
                output.append(child_html)

        index += 1

    return "\n".join(part for part in output if part)


def wrap_html(title: str, memo: str, body: str, tags: list[str]) -> str:
    """Build a standalone preview HTML document; not a Naver editor upload format."""
    tag_html = " ".join("#" + html.escape(tag) for tag in tags)
    safe_title = html.escape(title)
    safe_memo = html.escape(memo)

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
body {{ font-family: Arial, 'Malgun Gothic', sans-serif; line-height: 1.8; max-width: 780px; margin: 30px auto; padding: 0 18px; color: #222; }}
img {{ max-width: 100%; height: auto; }}
pre {{ overflow-x: auto; white-space: pre-wrap; background: #f3f3f3; padding: 14px; border-radius: 6px; }}
code {{ font-family: Consolas, monospace; }}
blockquote {{ border-left: 4px solid #888; padding: 8px 16px; margin-left: 0; background: #f7f7f7; }}
.callout {{ padding: 12px 16px; background: #f4f7fb; border-radius: 6px; }}
table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
figcaption {{ color: #666; font-size: .9em; }}
.todo {{ list-style: none; }}
.conversion-warning {{ color: #b45309; }}
</style>
</head>
<body>
<header>
<h1>{safe_title}</h1>
{f"<p>{safe_memo}</p>" if memo else ""}
{f'<p class="tags">{tag_html}</p>' if tag_html else ""}
</header>
<main>
{body}
</main>
</body>
</html>
"""

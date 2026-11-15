from __future__ import annotations

import hashlib
import html
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from pygments import lex
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.token import Token


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
    code_image_dir: Path | None = None,
    headings: list[dict[str, Any]] | None = None,
    heading_counts: dict[str, int] | None = None,
) -> tuple[str, int]:
    tag = "ul" if kind == "bulleted_list_item" else "ol"
    items: list[str] = []
    index = start
    while index < len(blocks) and blocks[index].get("type") == kind:
        block = blocks[index]
        data = block.get(kind) or {}
        item_html, raw = _text_fields(data)
        if raw:
            lines.append(("• " if tag == "ul" else f"{len(items) + 1}. ") + raw)
        nested = render_blocks(
            block.get("_children") or [], image_map, lines, depth + 1,
            code_image_dir, headings, heading_counts,
        )
        if item_html or nested:
            items.append(f"<li>{item_html}{nested}</li>")
        index += 1
    if not items:
        return "", index
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
            cell_html = rich_text(cell)
            cell_text = plain(cell).strip()
            if (has_column_header and row_index == 0) or (has_row_header and col_index == 0):
                tag = "th"
            else:
                tag = "td"
            rendered.append(f"<{tag}>{cell_html}</{tag}>")
            plain_cells.append(cell_text)
        if any(plain_cells):
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


_CODE_COLORS = {
    Token.Keyword: "#c792ea", Token.Name.Function: "#82aaff",
    Token.Name.Class: "#ffcb6b", Token.String: "#c3e88d",
    Token.Number: "#f78c6c", Token.Comment: "#91a0bd",
    Token.Operator: "#89ddff", Token.Punctuation: "#c5cbd6",
}


def _code_font(size: int = 16, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a Korean-capable font; prefer monospaced fonts for source code.

    GitHub Actions installs fonts-noto-cjk. The TTC index 6 is the Korean
    Noto Sans Mono CJK face on Ubuntu runners.
    """
    candidates: list[tuple[str, int]] = []
    override = os.getenv("CODE_FONT_PATH", "").strip()
    if override:
        candidates.append((override, 0))

    if bold:
        candidates.extend([
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 6),
            ("C:/Windows/Fonts/malgunbd.ttf", 0),
            ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
        ])
    else:
        candidates.extend([
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 6),
            ("/usr/share/fonts/truetype/nanum/NanumGothicCoding.ttf", 0),
            ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 0),
            ("C:/Windows/Fonts/D2Coding-Ver1.3.2-20180524.ttf", 0),
            ("C:/Windows/Fonts/malgun.ttf", 0),
            ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
            ("/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf", 0),
        ])

    for path, index in candidates:
        try:
            return ImageFont.truetype(path, size, index=index)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _token_color(token_type: Any) -> str:
    for token, color in _CODE_COLORS.items():
        if token_type in token:
            return color
    return "#e6e6e6"


def render_code_image(code: str, language: str, destination: Path) -> Path:
    """Render syntax-highlighted code to PNG, including Korean comments/strings."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    display_code = code.expandtabs(4)
    lines = display_code.splitlines() or [""]
    font = _code_font(17)
    label_font = _code_font(13, bold=True)

    width, line_height, pad, header = 1500, 29, 30, 52
    max_text_width = width - (pad * 2)

    try:
        lexer = get_lexer_by_name((language or "").lower(), stripnl=False, ensurenl=False)
    except Exception:
        lexer = TextLexer()

    # Tokenize to character-level rows so long lines can wrap without clipping.
    token_rows: list[list[tuple[str, Any]]] = [[]]
    for token_type, token_text in lex(display_code, lexer):
        for char in token_text:
            if char == "\r":
                continue
            if char == "\n":
                token_rows.append([])
            else:
                token_rows[-1].append((char, token_type))
    if len(token_rows) > 1 and not token_rows[-1]:
        token_rows.pop()

    measure_image = Image.new("RGB", (1, 1))
    measure_draw = ImageDraw.Draw(measure_image)
    wrapped_rows: list[list[tuple[str, Any]]] = []
    for row in token_rows:
        current: list[tuple[str, Any]] = []
        current_width = 0.0
        for char, token_type in row:
            char_width = measure_draw.textlength(char, font=font)
            if current and current_width + char_width > max_text_width:
                wrapped_rows.append(current)
                current, current_width = [], 0.0
            current.append((char, token_type))
            current_width += char_width
        wrapped_rows.append(current)

    max_rows = 400
    truncated = len(wrapped_rows) > max_rows
    if truncated:
        wrapped_rows = wrapped_rows[:max_rows - 1]
        wrapped_rows.append([("… 코드가 길어 이미지에서 일부 생략되었습니다. TXT 원고를 확인하세요.", Token.Text)])

    height = max(145, header + pad * 2 + len(wrapped_rows) * line_height)
    image = Image.new("RGB", (width, height), "#171a21")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 5, width - 5, height - 5), radius=14, fill="#171a21", outline="#303746", width=2)
    draw.rounded_rectangle((5, 5, width - 5, header + 5), radius=14, fill="#222733")
    draw.rectangle((5, header - 8, width - 5, header + 5), fill="#222733")
    draw.text((pad, 17), (language or "text").upper(), font=label_font, fill="#d4dbea")

    y = header + pad
    for row in wrapped_rows:
        x = pad
        for char, token_type in row:
            color = "#f0b36a" if truncated and char == "…" else _token_color(token_type)
            draw.text((x, y), char, font=font, fill=color)
            x += draw.textlength(char, font=font)
        y += line_height

    # Always overwrite: previously generated images may contain tofu glyphs
    # from a font that did not include Korean characters.
    image.save(destination, "PNG", optimize=True)
    return destination


def _slugify_heading(text: str, counts: dict[str, int]) -> str:
    base = re.sub(r"[^\w가-힣-]+", "-", text.lower(), flags=re.UNICODE)
    base = re.sub(r"-+", "-", base).strip("-") or "section"
    counts[base] = counts.get(base, 0) + 1
    return base if counts[base] == 1 else f"{base}-{counts[base]}"


def _is_toc_heading(text: str) -> bool:
    compact = re.sub(r"[^\w가-힣]+", "", text, flags=re.UNICODE).lower()
    return compact in {"목차", "contents", "tableofcontents"}


def render_blocks(
    blocks: list[dict], image_map: dict[str, str] | None,
    lines: list[str], depth: int = 0, code_image_dir: Path | None = None,
    headings: list[dict[str, Any]] | None = None,
    heading_counts: dict[str, int] | None = None,
) -> str:
    """Convert Notion blocks to clean semantic HTML and collect headings for a TOC."""
    if depth > 20:
        return '<p class="conversion-warning">[중첩 블록 깊이 제한]</p>'

    image_map = image_map or {}
    if headings is None:
        headings = []
    if heading_counts is None:
        heading_counts = {}

    output: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index] or {}
        kind = block.get("type", "")
        data = block.get(kind) or {}
        children = block.get("_children") or []
        value, raw = _text_fields(data)

        if kind in ("bulleted_list_item", "numbered_list_item"):
            rendered, index = _render_list(
                blocks, index, kind, image_map, lines, depth,
                code_image_dir, headings, heading_counts,
            )
            if rendered:
                output.append(rendered)
            continue

        if kind == "paragraph":
            if value:
                output.append(f"<p>{value}</p>")
                lines.append(raw)

        elif kind in ("heading_1", "heading_2", "heading_3"):
            if raw and not _is_toc_heading(raw):
                # The page title is the only H1. Notion body H1/H2/H3 become H2/H3/H4.
                level = int(kind[-1]) + 1
                heading_id = _slugify_heading(raw, heading_counts)
                headings.append({"level": level, "title": raw, "id": heading_id})
                output.append(f'<h{level} id="{heading_id}">{value}</h{level}>')
                lines.extend(["", raw, ""])

        elif kind == "to_do":
            if value:
                mark = "☑" if data.get("checked") else "☐"
                output.append(f'<p class="todo">{mark} {value}</p>')
                lines.append(f"{mark} {raw}")

        elif kind == "quote":
            if value:
                output.append(f"<blockquote>{value}</blockquote>")
                lines.append("> " + raw)

        elif kind == "callout":
            if value or children:
                icon = data.get("icon") or {}
                icon_text = icon.get("emoji") or "💡"
                nested = render_blocks(
                    children, image_map, lines, depth + 1, code_image_dir,
                    headings, heading_counts,
                )
                output.append(
                    f'<aside class="callout"><span>{html.escape(icon_text)}</span> '
                    f'{value}{nested}</aside>'
                )
                if raw:
                    lines.append(f"{icon_text} {raw}")

        elif kind == "code":
            code = plain(data.get("rich_text") or [])
            if code.strip():
                language_raw = str(data.get("language") or "text")
                if code_image_dir is not None:
                    safe_lang = re.sub(r"[^a-zA-Z0-9_-]+", "_", language_raw.lower())[:20] or "code"
                    digest = hashlib.sha1((language_raw + "\0" + code).encode("utf-8")).hexdigest()[:10]
                    image_path = code_image_dir / f"code_{safe_lang}_{digest}.png"
                    render_code_image(code, language_raw, image_path)
                    src = "images/" + image_path.name
                    output.append(
                        '<figure class="code-image">'
                        f'<img src="{html.escape(src, quote=True)}" '
                        f'alt="{html.escape(language_raw + " code", quote=True)}">'
                        f'<figcaption>{html.escape(language_raw)} 코드</figcaption></figure>'
                    )
                else:
                    output.append(
                        f'<pre data-language="{html.escape(language_raw, quote=True)}">'
                        f'<code>{html.escape(code)}</code></pre>'
                    )
                lines.extend(["", f"```{language_raw}", code, "```", ""])

        elif kind == "divider":
            output.append("<hr>")
            lines.append("──────────")

        elif kind == "image":
            caption_items = data.get("caption") or []
            caption = rich_text(caption_items)
            alt = html.escape(plain(caption_items), quote=True)
            src = image_map.get(block.get("id")) or _media_url(data)
            if src:
                caption_html = f"<figcaption>{caption}</figcaption>" if caption else ""
                output.append(
                    f'<figure><img src="{html.escape(src, quote=True)}" alt="{alt}">'
                    f"{caption_html}</figure>"
                )
                lines.append("[이미지: " + (plain(caption_items) or "설명 없음") + "]")
            else:
                output.append('<p class="conversion-warning">[이미지 다운로드 실패]</p>')

        elif kind in ("file", "pdf", "video", "audio", "bookmark", "embed", "link_preview"):
            url = _media_url(data) or data.get("url", "")
            label = data.get("name") or data.get("title") or kind
            if url:
                output.append(
                    f'<p class="attachment"><a href="{html.escape(url, quote=True)}">'
                    f'{html.escape(str(label))}</a></p>'
                )
                lines.append(f"[{kind}] {url}")

        elif kind == "table":
            table_html = _render_table(block, lines)
            if table_html:
                output.append(table_html)

        elif kind == "equation":
            expression = str(data.get("expression") or "").strip()
            if expression:
                output.append(f'<p class="equation"><code>{html.escape(expression)}</code></p>')
                lines.append(expression)

        elif kind == "toggle":
            nested = render_blocks(
                children, image_map, lines, depth + 1, code_image_dir,
                headings, heading_counts,
            )
            # Naver's editor does not reliably preserve HTML <details> toggles.
            # Expand the content and retain the toggle label as a bold paragraph.
            if raw:
                output.append(f'<p class="toggle-title"><strong>▸ {value}</strong></p>')
                lines.append(raw)
            if nested:
                output.append(nested)

        elif kind in ("column_list", "column", "synced_block", "template"):
            nested = render_blocks(
                children, image_map, lines, depth + 1, code_image_dir,
                headings, heading_counts,
            )
            if nested:
                output.append(f'<div class="notion-{kind}">{nested}</div>')

        elif kind in ("child_page", "child_database"):
            label = str(data.get("title") or "하위 페이지").strip()
            if label:
                output.append(f"<p><strong>{html.escape(label)}</strong></p>")
                lines.append(label)

        elif value:
            output.append(f"<p>{value}</p>")
            lines.append(raw)

        # Render children for block types whose children were not handled above.
        handled_children = {
            "callout", "toggle", "column_list", "column", "synced_block", "template",
            "table", "bulleted_list_item", "numbered_list_item",
        }
        if children and kind not in handled_children:
            nested = render_blocks(
                children, image_map, lines, depth + 1, code_image_dir,
                headings, heading_counts,
            )
            if nested:
                output.append(nested)
        index += 1

    return "\n".join(part for part in output if part.strip())


def _render_toc(headings: list[dict[str, Any]]) -> str:
    if not headings:
        return ""
    items = []
    for heading in headings:
        level = int(heading.get("level", 2))
        title = html.escape(str(heading.get("title", "")))
        heading_id = html.escape(str(heading.get("id", "")), quote=True)
        items.append(
            f'<li class="toc-level-{level}"><a href="#{heading_id}">{title}</a></li>'
        )
    return (
        '<nav class="table-of-contents" aria-label="목차">'
        '<h2>📚 목차</h2><ol>' + "".join(items) + "</ol></nav>"
    )


def wrap_html(
    title: str, memo: str, body: str, tags: list[str],
    headings: list[dict[str, Any]] | None = None,
) -> str:
    """Create a standalone browser preview; final Naver formatting must be checked in-editor."""
    tag_html = " ".join("#" + html.escape(tag) for tag in tags)
    safe_title = html.escape(title)
    safe_memo = html.escape(memo)
    toc_html = _render_toc(headings or [])
    memo_html = f"<p>{safe_memo}</p>" if memo else ""
    tags_html = f'<p class="tags">{tag_html}</p>' if tag_html else ""

    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
body {{ font-family: Arial, 'Malgun Gothic', 'Noto Sans CJK KR', sans-serif; line-height: 1.85; max-width: 820px; margin: 32px auto; padding: 0 20px; color: #222; }}
header {{ margin-bottom: 28px; }}
header h1 {{ font-size: 2em; line-height: 1.35; margin: 0 0 12px; }}
main h2 {{ margin-top: 2em; font-size: 1.55em; }}
main h3 {{ margin-top: 1.6em; font-size: 1.25em; }}
main h4 {{ margin-top: 1.3em; font-size: 1.08em; }}
p {{ margin: 0.8em 0; }}
img {{ max-width: 100%; height: auto; }}
figure {{ margin: 1.4em 0; }}
.code-image img {{ display:block; width:100%; border-radius:10px; }}
pre {{ overflow-x: auto; white-space: pre-wrap; background: #f3f3f3; padding: 14px; border-radius: 6px; }}
code {{ font-family: Consolas, 'Noto Sans Mono CJK KR', monospace; }}
blockquote {{ border-left: 4px solid #888; padding: 8px 16px; margin-left: 0; background: #f7f7f7; }}
.callout {{ padding: 12px 16px; background: #f4f7fb; border-radius: 6px; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
figcaption {{ color: #666; font-size: .9em; margin-top: 6px; }}
.todo {{ list-style: none; }}
.conversion-warning {{ color: #b45309; }}
a {{ overflow-wrap: anywhere; }}
.table-of-contents {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 20px; margin: 24px 0 32px; background: #fafafa; }}
.table-of-contents h2 {{ margin: 0 0 8px; font-size: 1.2em; }}
.table-of-contents ol {{ margin: 0; padding-left: 24px; }}
.table-of-contents li {{ margin: 4px 0; }}
.table-of-contents .toc-level-3 {{ margin-left: 18px; list-style-type: lower-alpha; }}
.table-of-contents .toc-level-4 {{ margin-left: 36px; list-style-type: circle; }}
.toggle-title {{ margin: 12px 0 4px; }}
.tags {{ color: #16803c; }}
</style>
</head>
<body>
<header><h1>{safe_title}</h1>{memo_html}{tags_html}</header>
{toc_html}
<main>
{body}
</main>
</body>
</html>
'''

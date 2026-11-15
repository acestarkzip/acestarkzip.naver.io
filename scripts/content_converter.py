from __future__ import annotations

import hashlib
import html
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse excessive blank lines while preserving deliberate paragraph breaks.
    value = re.sub(r"\n{3,}", "\n\n", value)
    # Rich-text blocks can contain intentional line breaks (especially callouts).
    value = value.replace("\n", "<br>\n")
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
    rich = data.get("rich_text")
    if not isinstance(rich, list):
        candidate = data.get("title")
        rich = candidate if isinstance(candidate, list) else []
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
    """Render a simple table using basic HTML that is easier to paste."""
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
            style = ' style="border:1px solid #d9d9d9;padding:7px 9px;text-align:left;"'
            rendered.append(f"<{tag}{style}>{cell_html}</{tag}>")
            plain_cells.append(cell_text)
        if any(plain_cells):
            output.append("<tr>" + "".join(rendered) + "</tr>")
            lines.append(" | ".join(plain_cells))

    if not output:
        return ""
    return (
        '<table style="border-collapse:collapse;width:100%;margin:14px 0;">'
        "<tbody>" + "".join(output) + "</tbody></table>"
    )


def _media_url(data: dict) -> str:
    media = data.get("file") or data.get("external") or {}
    if not isinstance(media, dict):
        media = {}
    return media.get("url") or data.get("url") or ""


# Dark editor palette inspired by familiar IDE syntax highlighting.
# Keep the dark charcoal canvas and use distinct, readable token colors.
_CODE_COLORS = {
    # More specific token types must come before their parent token types.
    Token.Comment.PreprocFile: "#D7BA7D",
    Token.Comment.Preproc: "#569CD6",
    Token.Keyword: "#569CD6",
    Token.Name.Class: "#DCDCAA",
    Token.Name.Function: "#DCDCAA",
    Token.Name.Builtin: "#4EC9B0",
    Token.Name.Builtin.Pseudo: "#9CDCFE",
    Token.Name.Namespace: "#D4D4D4",
    Token.Name: "#D4D4D4",
    Token.Literal.String: "#D7E39B",
    Token.String: "#D7E39B",
    Token.Literal.Number: "#B5CEA8",
    Token.Number: "#B5CEA8",
    Token.Comment: "#C5A37A",
    Token.Operator: "#D4D4D4",
    Token.Punctuation: "#D4D4D4",
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


def code_image_filename(code: str, language: str) -> str:
    """Return the stable filename used for a rendered code screenshot."""
    safe_lang = re.sub(r"[^a-zA-Z0-9_-]+", "_", (language or "text").lower())[:20] or "code"
    digest = hashlib.sha1(((language or "text") + "\0" + code).encode("utf-8")).hexdigest()[:10]
    return f"code_{safe_lang}_{digest}.png"


def render_code_image(code: str, language: str, destination: Path) -> Path:
    """Render syntax-highlighted code to PNG, including Korean comments/strings."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    display_code = code.expandtabs(4)
    lines = display_code.splitlines() or [""]
    # The image is displayed at roughly half its generated width in a typical
    # blog column, so use a larger source font to keep pasted code readable.
    font = _code_font(22)
    label_font = _code_font(15, bold=True)

    width, line_height, pad, header = 1200, 36, 30, 52
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
    image = Image.new("RGB", (width, height), "#1e1e1e")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 5, width - 5, height - 5), radius=14, fill="#1e1e1e", outline="#343434", width=2)
    draw.rounded_rectangle((5, 5, width - 5, header + 5), radius=14, fill="#252526")
    draw.rectangle((5, header - 8, width - 5, header + 5), fill="#252526")
    draw.text((pad, 17), (language or "text").upper(), font=label_font, fill="#d4d4d4")

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
    """Convert Notion blocks into a simple, copy-friendly Naver Blog article."""
    if depth > 20:
        return "<p>[중첩 콘텐츠 일부 생략]</p>"

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
            if value.strip():
                output.append(f"<p>{value}</p>")
                lines.append(raw.strip())

        elif kind in ("heading_1", "heading_2", "heading_3", "heading_4"):
            if raw.strip() and _is_toc_heading(raw):
                # Place the generated TOC exactly where the author put the TOC heading.
                output.append("<!-- NAVER_AUTO_TOC -->")
            elif raw.strip():
                # One page title (H1); all body headings use H2-H4 for a compact hierarchy.
                level = min(int(kind[-1]) + 1, 5)
                heading_id = _slugify_heading(raw, heading_counts)
                headings.append({"level": level, "title": raw.strip(), "id": heading_id})
                heading_style = {
                    2: "font-size:1.45em;font-weight:700;margin:1.6em 0 .55em;",
                    3: "font-size:1.2em;font-weight:700;margin:1.25em 0 .45em;",
                    4: "font-size:1.05em;font-weight:700;margin:1em 0 .35em;",
                    5: "font-size:.98em;font-weight:700;margin:.9em 0 .3em;",
                }[level]
                output.append(f'<h{level} style="{heading_style}">{value}</h{level}>')
                lines.extend(["", raw.strip(), ""])

        elif kind == "to_do":
            if value.strip():
                mark = "☑" if data.get("checked") else "☐"
                output.append(f"<p>{mark} {value}</p>")
                lines.append(f"{mark} {raw.strip()}")

        elif kind == "quote":
            if value.strip():
                output.append(
                    '<blockquote style="margin:12px 0;padding:8px 14px;'
                    'border-left:3px solid #999;color:#444;">'
                    f"{value}</blockquote>"
                )
                lines.append("> " + raw.strip())

        elif kind == "callout":
            if value.strip() or children:
                icon = data.get("icon") or {}
                icon_text = icon.get("emoji") or "💡"
                nested = render_blocks(
                    children, image_map, lines, depth + 1, code_image_dir,
                    headings, heading_counts,
                )
                content = f"<p><strong>{html.escape(icon_text)} {value}</strong></p>" if value else ""
                output.append(
                    '<blockquote style="margin:14px 0;padding:10px 14px;'
                    'border-left:4px solid #16a34a;background:#f7f9f7;">'
                    f"{content}{nested}</blockquote>"
                )
                if raw.strip():
                    lines.append(f"{icon_text} {raw.strip()}")

        elif kind == "code":
            code = plain(data.get("rich_text") or [])
            if code.strip():
                language_raw = str(data.get("language") or "text")
                if code_image_dir is not None:
                    image_path = code_image_dir / code_image_filename(code, language_raw)
                    render_code_image(code, language_raw, image_path)
                    src = "images/" + image_path.name
                    output.append(
                        f'<p><img src="{html.escape(src, quote=True)}" '
                        'style="display:block;max-width:100%;height:auto;margin:12px 0;" '
                        f'alt="{html.escape(language_raw + " 코드", quote=True)}"></p>'
                    )
                    output.append(f'<p><strong>{html.escape(language_raw.upper())} 코드</strong></p>')
                else:
                    output.append(
                        '<pre style="white-space:pre-wrap;overflow-wrap:anywhere;'
                        'background:#f5f5f5;padding:12px;border:1px solid #e5e5e5;">'
                        f'<code>{html.escape(code)}</code></pre>'
                    )
                lines.extend(["", f"```{language_raw}", code, "```", ""])

        elif kind == "divider":
            output.append('<hr style="border:0;border-top:1px solid #ddd;margin:22px 0;">')
            lines.append("──────────")

        elif kind == "image":
            caption_items = data.get("caption") or []
            caption = rich_text(caption_items)
            alt = html.escape(plain(caption_items), quote=True)
            src = image_map.get(block.get("id")) or _media_url(data)
            if src:
                output.append(
                    f'<p><img src="{html.escape(src, quote=True)}" alt="{alt}" '
                    'style="display:block;max-width:100%;height:auto;margin:14px auto;"></p>'
                )
                if caption:
                    output.append(f"<p><small>{caption}</small></p>")
                lines.append("[이미지: " + (plain(caption_items) or "설명 없음") + "]")
            else:
                output.append("<p>[이미지를 불러오지 못했습니다. 이미지 목록을 확인하세요.]</p>")

        elif kind in ("file", "pdf", "video", "audio", "bookmark", "embed", "link_preview"):
            url = _media_url(data) or data.get("url", "")
            caption_value = data.get("caption") or []
            title_value = data.get("title") or ""
            caption_text = plain(caption_value).strip() if isinstance(caption_value, list) else str(caption_value).strip()
            title_text = plain(title_value).strip() if isinstance(title_value, list) else str(title_value).strip()
            description_value = data.get("description") or ""
            description_text = (
                plain(description_value).strip()
                if isinstance(description_value, list) else str(description_value).strip()
            )
            label = data.get("name") or caption_text or title_text
            if not label and url:
                label = urlparse(url).netloc or url
            if not label:
                label = "첨부 파일" if kind in ("file", "pdf") else "관련 링크"
            if url:
                safe_url = html.escape(url, quote=True)
                safe_label = html.escape(str(label))
                output.append(f'<p>🔗 <a href="{safe_url}">{safe_label}</a></p>')
                if description_text and description_text != str(label):
                    output.append(f"<p>{html.escape(description_text)}</p>")
                lines.append(f"[{label}]({url})")

        elif kind == "table":
            table_html = _render_table(block, lines)
            if table_html:
                output.append(table_html)

        elif kind == "equation":
            expression = str(data.get("expression") or "").strip()
            if expression:
                output.append(f'<p><code>{html.escape(expression)}</code></p>')
                lines.append(expression)

        elif kind == "toggle":
            # Toggle UI is flattened to a bold label and visible body for simple pasting.
            nested = render_blocks(
                children, image_map, lines, depth + 1, code_image_dir,
                headings, heading_counts,
            )
            if raw.strip():
                output.append(f"<p><strong>▸ {value}</strong></p>")
                lines.append(raw.strip())
            if nested:
                # Indent nested content with a basic blockquote instead of a custom toggle widget.
                output.append(
                    '<blockquote style="margin:6px 0 12px 14px;padding-left:12px;'
                    'border-left:2px solid #ddd;">' + nested + "</blockquote>"
                )

        elif kind in ("column_list", "column", "synced_block", "template"):
            nested = render_blocks(
                children, image_map, lines, depth + 1, code_image_dir,
                headings, heading_counts,
            )
            if nested:
                output.append(nested)

        elif kind in ("child_page", "child_database"):
            label = str(data.get("title") or "하위 페이지").strip()
            if label:
                output.append(f"<p><strong>{html.escape(label)}</strong></p>")
                lines.append(label)

        elif value.strip():
            # Unknown blocks: keep readable text rather than emitting Notion-specific markup.
            output.append(f"<p>{value}</p>")
            lines.append(raw.strip())

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

    rendered = "\n".join(part for part in output if part.strip())
    if "<!-- NAVER_AUTO_TOC -->" in rendered:
        rendered = rendered.replace("<!-- NAVER_AUTO_TOC -->", _render_toc(headings))
    return rendered


def _plain_rich_text(items: list[dict] | str | None) -> str:
    """Create editor-friendly text; keep meaningful URLs visible and copyable."""
    if isinstance(items, str):
        return items.replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    for item in items or []:
        item = item or {}
        text_obj = item.get("text") or {}
        value = item.get("plain_text") or text_obj.get("content") or ""
        href = _safe_link(item)
        if href and href not in value:
            normalized = href.removeprefix("mailto:")
            if normalized not in value:
                value = f"{value} ({href})" if value else href
        parts.append(value)
    return "".join(parts).replace("\r\n", "\n").replace("\r", "\n")


def render_plain_text(
    blocks: list[dict],
    image_map: dict[str, str] | None = None,
    headings: list[dict[str, Any]] | None = None,
    code_sources: list[dict[str, str]] | None = None,
) -> str:
    """Render Notion blocks as plain text intended for Naver SmartEditor.

    No HTML, Markdown fences, CSS, or browser clipboard formatting is emitted.
    Media placeholders show where the user should upload each local image.
    """
    image_map = image_map or {}
    headings = headings or []
    if code_sources is None:
        code_sources = []

    def visible_toc() -> list[str]:
        rows: list[str] = []
        for heading in headings:
            title = str(heading.get("title", "")).strip()
            compact = re.sub(r"[^\w가-힣]+", "", title, flags=re.UNICODE).lower()
            if not title or compact in {"목차", "contents", "tableofcontents", "contactus", "연락처"}:
                continue
            level = max(2, min(int(heading.get("level", 2)), 5))
            rows.append("  " * (level - 2) + "• " + title)
        return rows

    toc_rows = visible_toc()
    toc_written = False

    def render(items: list[dict], depth: int = 0) -> list[str]:
        nonlocal toc_written
        result: list[str] = []
        index = 0
        indent = "  " * depth

        while index < len(items):
            block = items[index] or {}
            kind = block.get("type", "")
            data = block.get(kind) or {}
            children = block.get("_children") or []
            rich = data.get("rich_text") or data.get("title") or []
            raw = _plain_rich_text(rich).strip()

            if kind in ("bulleted_list_item", "numbered_list_item"):
                run_kind = kind
                count = 0
                while index < len(items) and items[index].get("type") == run_kind:
                    item = items[index] or {}
                    item_data = item.get(run_kind) or {}
                    item_text = _plain_rich_text(item_data.get("rich_text") or item_data.get("title") or []).strip()
                    item_children = item.get("_children") or []
                    if item_text:
                        count += 1
                        prefix = "• " if run_kind == "bulleted_list_item" else f"{count}. "
                        result.append(indent + prefix + item_text)
                    result.extend(render(item_children, depth + 1))
                    index += 1
                result.append("")
                continue

            if kind == "paragraph":
                if raw:
                    result.extend(indent + line for line in raw.split("\n"))
                    result.append("")

            elif kind in ("heading_1", "heading_2", "heading_3", "heading_4"):
                if raw:
                    compact = re.sub(r"[^\w가-힣]+", "", raw, flags=re.UNICODE).lower()
                    if compact in {"목차", "contents", "tableofcontents"}:
                        result.append(indent + (raw if "목차" in raw else "📚 목차"))
                        if toc_rows and not toc_written:
                            result.extend(indent + row for row in toc_rows)
                            toc_written = True
                        result.append("")
                    else:
                        original_level = int(kind[-1])
                        result.append("  " * max(0, original_level - 1) + raw)
                        result.append("")

            elif kind == "to_do":
                if raw:
                    mark = "☑" if data.get("checked") else "☐"
                    result.append(indent + f"{mark} {raw}")

            elif kind == "quote":
                if raw:
                    result.append(indent + f"“{raw}”")
                    result.append("")

            elif kind == "callout":
                icon = (data.get("icon") or {}).get("emoji") or "💡"
                if raw:
                    result.extend(indent + line for line in (f"{icon} {raw}").split("\n"))
                result.extend(render(children, depth + 1))
                if raw or children:
                    result.append("")

            elif kind == "code":
                code = _plain_rich_text(data.get("rich_text") or [])
                if code.strip():
                    language = str(data.get("language") or "text")
                    filename = code_image_filename(code, language)
                    result.append(indent + f"[코드 이미지 삽입 위치: images/{filename}]")
                    result.append(indent + "(코드 원문이 필요하면 05_코드_원문.txt를 참고하세요.)")
                    code_sources.append({"language": language, "code": code, "filename": filename})
                    result.append("")

            elif kind == "divider":
                result.extend([indent + "────────────────────────", ""])

            elif kind == "image":
                caption = _plain_rich_text(data.get("caption") or []).strip()
                local_path = image_map.get(block.get("id"), "")
                if local_path:
                    result.append(indent + f"[이미지 삽입 위치: {local_path}]")
                else:
                    result.append(indent + "[이미지 파일 확인 필요: 이미지 목록 참조]")
                if caption:
                    result.append(indent + caption)
                result.append("")

            elif kind in ("file", "pdf", "video", "audio", "bookmark", "embed", "link_preview"):
                url = _media_url(data) or data.get("url", "")
                caption_value = data.get("caption") or []
                title_value = data.get("title") or ""
                description_value = data.get("description") or ""
                caption = _plain_rich_text(caption_value).strip() if isinstance(caption_value, list) else str(caption_value).strip()
                title = _plain_rich_text(title_value).strip() if isinstance(title_value, list) else str(title_value).strip()
                description = _plain_rich_text(description_value).strip() if isinstance(description_value, list) else str(description_value).strip()
                label = data.get("name") or caption or title or (urlparse(url).netloc if url else "관련 링크")
                local_path = image_map.get(block.get("id"), "")
                if local_path:
                    result.append(indent + f"[첨부 파일 확인: {local_path}]")
                elif url:
                    result.append(indent + f"🔗 {label}")
                    result.append(indent + url)
                elif label:
                    result.append(indent + str(label))
                if description and description != str(label):
                    result.append(indent + description)
                if url or local_path or description:
                    result.append("")

            elif kind == "table":
                for row in children:
                    cells = (row.get("table_row") or {}).get("cells") or []
                    cell_texts = [_plain_rich_text(cell).strip() for cell in cells]
                    if any(cell_texts):
                        result.append(indent + " | ".join(cell_texts))
                if children:
                    result.append("")

            elif kind == "equation":
                expression = str(data.get("expression") or "").strip()
                if expression:
                    result.extend([indent + expression, ""])

            elif kind == "toggle":
                if raw:
                    result.append(indent + f"▸ {raw}")
                result.extend(render(children, depth + 1))
                if raw or children:
                    result.append("")

            elif kind in ("column_list", "column", "synced_block", "template"):
                result.extend(render(children, depth))

            elif kind in ("child_page", "child_database"):
                label = str(data.get("title") or "하위 페이지").strip()
                if label:
                    result.extend([indent + label, ""])

            elif raw:
                result.extend(indent + line for line in raw.split("\n"))
                result.append("")

            if children and kind not in {
                "callout", "toggle", "column_list", "column", "synced_block", "template",
                "table", "bulleted_list_item", "numbered_list_item",
            }:
                result.extend(render(children, depth + 1))
            index += 1

        return result

    output = render(blocks)
    cleaned: list[str] = []
    previous_blank = False
    for line in output:
        line = line.rstrip()
        if not line:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
        else:
            cleaned.append(line)
            previous_blank = False
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned)

def _render_toc(headings: list[dict[str, Any]]) -> str:
    # Create a simple text/list TOC; no internal anchors are required for Naver.
    visible_headings = [
        heading for heading in headings
        if re.sub(r"[^\w가-힣]+", "", str(heading.get("title", "")), flags=re.UNICODE).lower()
        not in {"contactus", "연락처"}
    ]
    if len(visible_headings) < 2:
        return ""

    items = []
    for heading in visible_headings:
        level = max(2, min(int(heading.get("level", 2)), 5))
        indent = (level - 2) * 16
        title = html.escape(str(heading.get("title", "")))
        items.append(
            f'<li style="margin:4px 0 4px {indent}px;">{title}</li>'
        )
    return (
        '<p style="margin:18px 0 6px;"><strong>📚 목차</strong></p>'
        '<ul style="list-style-type:disc;margin:0 0 22px;padding-left:24px;">'
        + "".join(items) + "</ul>"
    )


def wrap_html(
    title: str, body: str, tags: list[str],
    headings: list[dict[str, Any]] | None = None,
) -> str:
    """Create a visual-only preview. Naver publishing uses the generated TXT."""
    safe_title = html.escape(title)
    tag_html = " ".join("#" + html.escape(tag) for tag in tags)
    tags_html = f'<p class="tags">{tag_html}</p>' if tag_html else ""

    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
body {{ font-family: Arial, 'Malgun Gothic', 'Noto Sans CJK KR', sans-serif; line-height: 1.8; max-width: 820px; margin: 32px auto; padding: 0 20px; color: #222; }}
.preview-note {{ padding: 10px 14px; margin-bottom: 20px; background: #f4f7fb; border-left: 4px solid #64748b; color: #475569; font-size: 14px; }}
header h1 {{ font-size: 2em; line-height: 1.35; margin: 0 0 18px; }}
article h2 {{ font-size: 1.55em; margin: 1.8em 0 .6em; }}
article h3 {{ font-size: 1.25em; margin: 1.5em 0 .5em; }}
article h4 {{ font-size: 1.08em; margin: 1.3em 0 .4em; }}
article h5 {{ font-size: 1em; margin: 1.1em 0 .35em; }}
article p {{ margin: .8em 0; }}
article img {{ display:block; max-width:100%; height:auto; margin:14px auto; }}
article ul, article ol {{ padding-left:26px; margin:10px 0; }}
article li {{ margin:5px 0; }}
article a {{ color:#145da0; text-decoration:underline; overflow-wrap:anywhere; }}
article pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#f5f5f5; padding:12px; }}
article blockquote {{ margin:12px 0; padding:8px 14px; border-left:3px solid #999; color:#444; }}
.tags {{ margin-top:24px; color:#16803c; }}
@media print {{ .preview-note {{ display:none; }} body {{ max-width:none; margin:0; }} }}
</style>
</head>
<body>
<div class="preview-note">이 파일은 미리보기 전용입니다. 네이버 블로그에는 같은 폴더의 01_네이버_제목.txt와 02_네이버_본문.txt를 사용하세요.</div>
<header><h1>{safe_title}</h1></header>
<article>
{body}
{tags_html}
</article>
</body>
</html>
'''

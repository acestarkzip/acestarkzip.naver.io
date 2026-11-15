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
                level = min(int(kind[-1]) + 1, 4)
                heading_id = _slugify_heading(raw, heading_counts)
                headings.append({"level": level, "title": raw.strip(), "id": heading_id})
                heading_style = {
                    2: "font-size:1.45em;font-weight:700;margin:1.6em 0 .55em;",
                    3: "font-size:1.2em;font-weight:700;margin:1.25em 0 .45em;",
                    4: "font-size:1.05em;font-weight:700;margin:1em 0 .35em;",
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
                    safe_lang = re.sub(r"[^a-zA-Z0-9_-]+", "_", language_raw.lower())[:20] or "code"
                    digest = hashlib.sha1((language_raw + "\0" + code).encode("utf-8")).hexdigest()[:10]
                    image_path = code_image_dir / f"code_{safe_lang}_{digest}.png"
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
        level = max(2, min(int(heading.get("level", 2)), 4))
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
    # Create a simple preview with separate title/body copy controls.
    safe_title = html.escape(title)
    tag_html = " ".join("#" + html.escape(tag) for tag in tags)
    # Database date/memo fields stay in metadata.json and are not inserted into the public post.
    tags_html = f'<p style="margin-top:20px;color:#16803c;">{tag_html}</p>' if tag_html else ""

    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
body {{ font-family: Arial, 'Malgun Gothic', 'Noto Sans CJK KR', sans-serif; line-height: 1.75; max-width: 820px; margin: 28px auto; padding: 0 18px; color: #222; }}
.copy-tools {{ position: sticky; top: 0; z-index: 10; padding: 10px; margin: 0 -18px 20px; background: #f3f6f3; border-bottom: 1px solid #dfe6df; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }}
.copy-tools button {{ border: 0; border-radius: 6px; background: #16803c; color: white; padding: 10px 16px; font-weight: 700; cursor: pointer; }}
.copy-tools small {{ color: #555; }}
.preview-title {{ font-size: 30px; line-height: 1.35; margin: 8px 0 18px; }}
article h2 {{ font-size: 24px; margin: 30px 0 12px; }}
article h3 {{ font-size: 20px; margin: 24px 0 9px; }}
article h4 {{ font-size: 17px; margin: 18px 0 8px; }}
article p {{ margin: 10px 0; }}
article img {{ max-width: 100%; height: auto; }}
article ul, article ol {{ padding-left: 26px; margin: 10px 0; }}
article li {{ margin: 5px 0; }}
article a {{ color: #145da0; text-decoration: underline; }}
article pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #f5f5f5; padding: 12px; }}
article blockquote {{ margin: 12px 0; }}
@media print {{ .copy-tools {{ display:none; }} body {{ max-width:none; margin:0; }} }}
</style>
</head>
<body>
<div class="copy-tools" aria-label="원고 복사 도구">
  <button type="button" id="copyTitleButton">제목 복사</button>
  <button type="button" id="copyBodyButton">본문 복사</button>
  <small id="copyStatus">제목과 본문을 각각 복사해 네이버 블로그 제목란과 본문에 붙여넣으세요.</small>
</div>
<h1 class="preview-title" id="naverTitle">{safe_title}</h1>
<article id="naverBody">
<main>
{body}
</main>
{tags_html}
</article>
<script>
(function () {{
  const status = document.getElementById('copyStatus');
  function copySelection(element, message) {{
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    let copied = false;
    try {{ copied = document.execCommand('copy'); }} catch (error) {{ copied = false; }}
    selection.removeAllRanges();
    status.textContent = copied
      ? message
      : '자동 복사에 실패했습니다. 해당 영역을 선택해 Ctrl+C 하세요.';
  }}
  document.getElementById('copyTitleButton').addEventListener('click', function () {{
    copySelection(document.getElementById('naverTitle'), '제목을 복사했습니다. 네이버 블로그 제목란에 붙여넣으세요.');
  }});
  document.getElementById('copyBodyButton').addEventListener('click', function () {{
    copySelection(document.getElementById('naverBody'), '본문을 복사했습니다. 네이버 블로그 본문에 붙여넣으세요.');
  }});
}})();
</script>
</body>
</html>
'''

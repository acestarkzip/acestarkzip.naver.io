from __future__ import annotations

import html
import re
import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from pygments import lex
from pygments.lexers import get_lexer_by_name, TextLexer
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
    code_image_dir: Path | None = None,
) -> tuple[str, int]:
    tag = "ul" if kind == "bulleted_list_item" else "ol"
    items: list[str] = []
    index = start
    while index < len(blocks) and blocks[index].get("type") == kind:
        block = blocks[index]
        data = block.get(kind) or {}
        item_html, raw = _text_fields(data)
        nested = render_blocks(block.get("_children") or [], image_map, lines, depth + 1, code_image_dir)
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



_CODE_COLORS = {
    Token.Keyword: "#c792ea", Token.Name.Function: "#82aaff",
    Token.Name.Class: "#ffcb6b", Token.String: "#c3e88d",
    Token.Number: "#f78c6c", Token.Comment: "#697098",
    Token.Operator: "#89ddff", Token.Punctuation: "#c5cbd6",
}
def _code_font(size=16):
    for name in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                 "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"):
        try: return ImageFont.truetype(name, size)
        except OSError: pass
    return ImageFont.load_default()

def render_code_image(code: str, language: str, destination: Path) -> Path:
    """Create a dark syntax-highlighted PNG. Source remains in the TXT export."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists(): return destination
    lines = code.expandtabs(4).splitlines() or [""]
    font, label_font = _code_font(16), _code_font(12)
    line_height, pad, header = 27, 28, 48
    width = 1200
    height = min(6000, max(145, header + pad*2 + len(lines)*line_height))
    image = Image.new("RGB", (width, height), "#171a21")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5,5,width-5,height-5), radius=14, fill="#171a21", outline="#303746", width=2)
    draw.rounded_rectangle((5,5,width-5,header+5), radius=14, fill="#222733")
    draw.rectangle((5,header-8,width-5,header+5), fill="#222733")
    draw.text((pad,18), (language or "text").upper(), font=label_font, fill="#aab6ca")
    try: lexer = get_lexer_by_name((language or "").lower(), stripnl=False, ensurenl=False)
    except Exception: lexer = TextLexer()
    tokens = list(lex(code, lexer))
    token_rows = [[]]
    for token_type, token_text in tokens:
        parts = token_text.splitlines(keepends=True)
        for part in parts:
            value = part.rstrip("\r\n")
            if value: token_rows[-1].append((value, token_type))
            if part.endswith(("\n", "\r")): token_rows.append([])
    if token_rows and not token_rows[-1]: token_rows.pop()
    y = header + pad
    if len(token_rows) == len(lines):
        for row in token_rows:
            x = pad
            for value, token_type in row:
                color = next((v for k,v in _CODE_COLORS.items() if token_type in k), "#e6e6e6")
                draw.text((x,y), value, font=font, fill=color)
                x += draw.textlength(value, font=font)
            y += line_height
    else:
        for line in lines:
            draw.text((pad,y), line[:125], font=font, fill="#e6e6e6")
            y += line_height
    image.save(destination, "PNG", optimize=True)
    return destination


def render_blocks(
    blocks: list[dict], image_map: dict[str, str] | None,
    lines: list[str], depth: int = 0, code_image_dir: Path | None = None,
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
            rendered, index = _render_list(
                blocks, index, kind, image_map, lines, depth, code_image_dir
            )
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
            language_raw = str(data.get("language") or "text")
            if code_image_dir is not None:
                safe_lang = re.sub(r"[^a-zA-Z0-9_-]+", "_", language_raw.lower())[:20] or "code"
                digest = hashlib.sha1((language_raw + "\\0" + code).encode("utf-8")).hexdigest()[:10]
                image_path = code_image_dir / f"code_{safe_lang}_{digest}.png"
                render_code_image(code, language_raw, image_path)
                src = "images/" + image_path.name
                output.append(f'<figure class="code-image"><img src="{html.escape(src, quote=True)}" alt="{html.escape(language_raw + " code", quote=True)}"><figcaption>{html.escape(language_raw)} 코드</figcaption></figure>')
            else:
                output.append(f'<pre data-language="{html.escape(language_raw, quote=True)}"><code>{html.escape(code)}</code></pre>')
            lines.extend(["", f"```{language_raw}", code, "```", ""])
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
            nested = render_blocks(children, image_map, lines, depth + 1, code_image_dir)
            output.append(f"<details><summary>{summary}</summary>{nested}</details>")
            if raw:
                lines.append(raw)
        elif kind in ("column_list", "column", "synced_block", "template"):
            nested = render_blocks(children, image_map, lines, depth + 1, code_image_dir)
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
            nested = render_blocks(children, image_map, lines, depth + 1, code_image_dir)
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
img {{ max-width: 100%; height: auto; }}\n.code-image img {{ display:block; width:100%; border-radius:10px; }}
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

from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

HEADING_SIZES = {1: 30, 2: 28, 3: 24, 4: 19}
BODY_SIZE = 15
YELLOW = WD_COLOR_INDEX.YELLOW
URL_PATTERN = re.compile(r"https?://[^\s<>]+|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
IMAGE_MARKER = re.compile(r"🟨\s*이미지\s*\d{2}\s*삽입\s*위치\s*:")


def _set_run_font(run, size: int, *, bold: bool = False, color: str = "000000") -> None:
    run.font.name = "Noto Sans CJK KR"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Noto Sans CJK KR")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def _add_hyperlink(paragraph, text: str, url: str):
    part = paragraph.part
    relation_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relation_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1A5FB4")
    props.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(underline)
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), str(BODY_SIZE * 2))
    props.append(size)
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), "Noto Sans CJK KR")
    fonts.set(qn("w:eastAsia"), "Noto Sans CJK KR")
    props.append(fonts)
    run.append(props)
    text_node = OxmlElement("w:t")
    text_node.text = text
    run.append(text_node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _add_text_with_links(paragraph, text: str) -> None:
    cursor = 0
    for match in URL_PATTERN.finditer(text):
        start, end = match.span()
        if start > cursor:
            run = paragraph.add_run(text[cursor:start])
            _set_run_font(run, BODY_SIZE)
        target = match.group(0).rstrip(".,;:!?)】］>")
        suffix = match.group(0)[len(target):]
        url = target if target.lower().startswith("http") else f"mailto:{target}"
        _add_hyperlink(paragraph, target, url)
        if suffix:
            run = paragraph.add_run(suffix)
            _set_run_font(run, BODY_SIZE)
        cursor = end
    if cursor < len(text):
        run = paragraph.add_run(text[cursor:])
        _set_run_font(run, BODY_SIZE)
    if not text:
        run = paragraph.add_run("")
        _set_run_font(run, BODY_SIZE)


def _heading_lookup(headings: list[dict[str, Any]] | None):
    lookup = defaultdict(deque)
    for item in headings or []:
        try:
            level = int(item.get("source_level", max(1, int(item.get("level", 2)) - 1)))
        except (TypeError, ValueError):
            level = 1
        title = str(item.get("title", "")).strip()
        if title and level in HEADING_SIZES:
            leading_spaces = 2 * (level - 1)
            lookup[(leading_spaces, title)].append(level)
    return lookup


def write_naver_docx(
    text: str,
    destination: Path,
    headings: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a Word copy-source with requested heading sizes and yellow image markers."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.75)
    section.left_margin = Inches(0.85)
    section.right_margin = Inches(0.85)

    normal = document.styles["Normal"]
    normal.font.name = "Noto Sans CJK KR"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Noto Sans CJK KR")
    normal.font.size = Pt(BODY_SIZE)
    normal.font.color.rgb = RGBColor(0, 0, 0)

    heading_lookup = _heading_lookup(headings)
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        leading_spaces = len(line) - len(line.lstrip(" "))
        content = line.strip()
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(5)
        paragraph.paragraph_format.line_spacing = 1.25

        if not content:
            paragraph.paragraph_format.space_after = Pt(3)
            continue

        is_image_marker = bool(IMAGE_MARKER.search(content))
        if is_image_marker:
            run = paragraph.add_run(content)
            _set_run_font(run, BODY_SIZE, bold=True, color="000000")
            run.font.highlight_color = YELLOW
            paragraph.paragraph_format.left_indent = Inches(0.08)
            paragraph.paragraph_format.space_before = Pt(5)
            paragraph.paragraph_format.space_after = Pt(8)
            continue

        # The plain-text renderer indents actual source headings by two spaces per level.
        key = (leading_spaces, content)
        level = None
        if heading_lookup.get(key):
            level = heading_lookup[key].popleft()

        # The generated TOC label was a Notion heading placeholder, so make it readable too.
        if content == "📚 목차":
            level = 1

        if level in HEADING_SIZES:
            run = paragraph.add_run(content)
            _set_run_font(run, HEADING_SIZES[level], bold=True, color="000000")
            paragraph.paragraph_format.left_indent = Inches(0.12 * (level - 1))
            paragraph.paragraph_format.space_before = Pt(18 if level <= 2 else 12)
            paragraph.paragraph_format.space_after = Pt(7)
            paragraph.paragraph_format.keep_with_next = True
            continue

        if leading_spaces:
            paragraph.paragraph_format.left_indent = Pt(min(leading_spaces * 3.5, 50))
        _add_text_with_links(paragraph, content)

    document.core_properties.title = "SSS Robotics - Naver Blog Draft"
    document.core_properties.subject = "Notion to Naver Blog formatted draft"
    document.core_properties.author = "SSS Robotics"
    document.save(destination)
    return destination

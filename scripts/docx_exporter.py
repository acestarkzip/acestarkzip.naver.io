from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

# Notion heading levels are mapped to the requested Naver draft sizes.
HEADING_SIZES = {1: 30, 2: 28, 3: 24, 4: 19}
BODY_SIZE = 15
FONT_NAME = "Malgun Gothic"

# The existing TXT marker strings are preserved exactly; only the DOCX copy
# version adds the yellow text highlight so it is visible when opened in Word.
IMAGE_MARKER = re.compile(
    r"^\s*(?:🟨\s*)?\[\s*(?:코드\s*)?이미지\s*삽입\s*위치\s*:\s*.+?\s*\]\s*$"
)
IMAGE_MISSING_MARKER = re.compile(r"^\s*\[\s*이미지\s*파일\s*확인\s*필요\s*:.*\]\s*$")
URL_PATTERN = re.compile(r"https?://[^\s<>]+|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def _set_font(run, size: int = BODY_SIZE, *, bold: bool = False) -> None:
    run.font.name = FONT_NAME
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT_NAME)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor(0, 0, 0)


def _add_hyperlink(paragraph, text: str, url: str) -> None:
    """Add an ordinary clickable URL in the DOCX without changing the TXT."""
    relation_id = paragraph.part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relation_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), FONT_NAME)
    fonts.set(qn("w:eastAsia"), FONT_NAME)
    props.append(fonts)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    props.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(underline)
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), str(BODY_SIZE * 2))
    props.append(size)
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
            _set_font(run)
        target = match.group(0).rstrip(".,;:!?)】］>")
        suffix = match.group(0)[len(target):]
        url = target if target.lower().startswith("http") else f"mailto:{target}"
        _add_hyperlink(paragraph, target, url)
        if suffix:
            run = paragraph.add_run(suffix)
            _set_font(run)
        cursor = end
    if cursor < len(text):
        run = paragraph.add_run(text[cursor:])
        _set_font(run)
    if not text:
        run = paragraph.add_run("")
        _set_font(run)


def _heading_lookup(headings: list[dict[str, Any]] | None):
    lookup = defaultdict(deque)
    for item in headings or []:
        try:
            # render_blocks stores HTML heading level = source Notion level + 1.
            source_level = int(item.get("source_level", max(1, int(item.get("level", 2)) - 1)))
        except (TypeError, ValueError):
            continue
        title = str(item.get("title", "")).strip()
        if title and source_level in HEADING_SIZES:
            lookup[(2 * (source_level - 1), title)].append(source_level)
    return lookup


def write_naver_docx(
    text: str,
    destination: Path,
    headings: list[dict[str, Any]] | None = None,
) -> Path:
    """Create a Word copy-source with yellow highlights on image placeholders."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.75)
    section.left_margin = Inches(0.85)
    section.right_margin = Inches(0.85)

    normal = document.styles["Normal"]
    normal.font.name = FONT_NAME
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
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

        if IMAGE_MARKER.match(content) or IMAGE_MISSING_MARKER.match(content):
            run = paragraph.add_run(content)
            _set_font(run, BODY_SIZE, bold=False)
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            paragraph.paragraph_format.space_before = Pt(5)
            paragraph.paragraph_format.space_after = Pt(8)
            continue

        level = None
        if content == "📚 목차":
            level = 1
        else:
            key = (leading_spaces, content)
            if heading_lookup.get(key):
                level = heading_lookup[key].popleft()

        if level in HEADING_SIZES:
            run = paragraph.add_run(content)
            _set_font(run, HEADING_SIZES[level], bold=True)
            paragraph.paragraph_format.left_indent = Inches(0.12 * (level - 1))
            paragraph.paragraph_format.space_before = Pt(18 if level <= 2 else 12)
            paragraph.paragraph_format.space_after = Pt(7)
            paragraph.paragraph_format.keep_with_next = True
            continue

        if leading_spaces:
            paragraph.paragraph_format.left_indent = Pt(min(leading_spaces * 3.5, 50))
        _add_text_with_links(paragraph, content)

    document.core_properties.title = "SSS Robotics - Naver Blog Draft"
    document.core_properties.subject = "Naver Blog copy source with highlighted image insertion markers"
    document.core_properties.author = "SSS Robotics"
    document.save(destination)
    return destination

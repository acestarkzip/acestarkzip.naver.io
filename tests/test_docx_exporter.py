from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from docx_exporter import write_naver_docx, HEADING_SIZES, BODY_SIZE
from docx import Document
from docx.enum.text import WD_COLOR_INDEX


def test_docx_has_requested_heading_sizes_and_yellow_image_placeholders(tmp_path):
    text = "📚 목차\n• 섹션 1\n\n제목 1\n  제목 2\n    제목 3\n      제목 4\n\n일반 본문입니다.\n🟨 이미지 01 삽입 위치: images/이미지_01_대표.png\n"
    headings = [
        {"source_level": 1, "title": "제목 1"},
        {"source_level": 2, "title": "제목 2"},
        {"source_level": 3, "title": "제목 3"},
        {"source_level": 4, "title": "제목 4"},
    ]
    destination = write_naver_docx(text, tmp_path / "draft.docx", headings)
    doc = Document(destination)
    sizes_by_text = {
        paragraph.text: next((run.font.size.pt for run in paragraph.runs if run.font.size), None)
        for paragraph in doc.paragraphs
    }
    assert sizes_by_text["제목 1"] == HEADING_SIZES[1]
    assert sizes_by_text["제목 2"] == HEADING_SIZES[2]
    assert sizes_by_text["제목 3"] == HEADING_SIZES[3]
    assert sizes_by_text["제목 4"] == HEADING_SIZES[4]
    assert sizes_by_text["일반 본문입니다."] == BODY_SIZE
    marker = next(p for p in doc.paragraphs if "이미지 01 삽입 위치" in p.text)
    assert marker.runs[0].font.highlight_color == WD_COLOR_INDEX.YELLOW


def test_docx_converts_urls_to_clickable_hyperlinks(tmp_path):
    destination = write_naver_docx(
        "YouTube: https://www.youtube.com/@sssrobotics\nEmail: hello@example.com\n",
        tmp_path / "links.docx",
    )
    doc = Document(destination)
    xml = "\n".join(p._p.xml for p in doc.paragraphs)
    assert "https://www.youtube.com/@sssrobotics" in xml
    external_targets = [rel.target_ref for rel in doc.part.rels.values() if rel.is_external]
    assert "mailto:hello@example.com" in external_targets

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from content_converter import plain, rich_text, render_blocks, wrap_html


def rt(text, link=None, **annotations):
    return {"type": "text", "plain_text": text, "text": {"content": text, "link": link}, "annotations": annotations}


def test_null_link_is_safe():
    assert rich_text([rt("일반 텍스트", None)]) == "일반 텍스트"


def test_link_and_marks():
    item = rt("OpenAI", {"url": "https://openai.com"}, bold=True)
    assert rich_text([item]) == '<a href="https://openai.com"><strong>OpenAI</strong></a>'


def test_headings_and_code():
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": [rt("제목 1")]}, "_children": []},
        {"type": "heading_2", "heading_2": {"rich_text": [rt("제목 2")]}, "_children": []},
        {"type": "code", "code": {"rich_text": [rt("print('한글')")], "language": "python"}, "_children": []},
    ]
    html = render_blocks(blocks, {}, [])
    assert '<h2 id="제목-1">제목 1</h2>' in html
    assert '<h3 id="제목-2">제목 2</h3>' in html
    assert "print(&#x27;한글&#x27;)" in html or "print('한글')" in html


def test_nested_list():
    blocks = [{
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [rt("상위 항목")]},
        "_children": [{
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [rt("하위 항목")]},
            "_children": [],
        }],
    }]
    html = render_blocks(blocks, {}, [])
    assert "상위 항목" in html and "하위 항목" in html
    assert html.count("<ul>") == 2


def test_table():
    block = {
        "type": "table",
        "table": {"has_column_header": True},
        "_children": [{
            "type": "table_row",
            "table_row": {"cells": [[rt("이름")], [rt("값")]]},
            "_children": [],
        }, {
            "type": "table_row",
            "table_row": {"cells": [[rt("로봇")], [rt("1")]]},
            "_children": [],
        }],
    }
    html = render_blocks([block], {}, [])
    assert "<table>" in html and "<th>이름</th>" in html and "<td>로봇</td>" in html


def test_image_map():
    block = {"id": "img-1", "type": "image", "image": {"caption": [rt("테스트 이미지")]}, "_children": []}
    html = render_blocks([block], {"img-1": "images/test.png"}, [])
    assert 'src="images/test.png"' in html
    assert "테스트 이미지" in html


def test_wrap_html():
    output = wrap_html("테스트", "메모", "<p>본문</p>", ["로봇"])
    assert "<title>테스트</title>" in output
    assert "#로봇" in output and "<p>본문</p>" in output


def test_body_headings_are_demoted_and_toc_is_generated():
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": [rt("📚 목차")]}},
        {"type": "heading_1", "heading_1": {"rich_text": [rt("오늘의 진행 내용")]}},
        {"type": "heading_2", "heading_2": {"rich_text": [rt("세부 주제")]}},
        {"type": "heading_3", "heading_3": {"rich_text": [rt("추가 설명")]}},
    ]
    headings = []
    body = render_blocks(blocks, {}, [], headings=headings)
    output = wrap_html("테스트 글", "", body, [], headings)

    assert output.count("<h1") == 1
    assert '<h2 id="오늘의-진행-내용">오늘의 진행 내용</h2>' in output
    assert '<h3 id="세부-주제">세부 주제</h3>' in output
    assert '<h4 id="추가-설명">추가 설명</h4>' in output
    assert '<a href="#오늘의-진행-내용">오늘의 진행 내용</a>' in output
    assert output.count("📚 목차") == 1


def test_empty_blocks_and_empty_caption_are_removed():
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": []}},
        {"type": "heading_2", "heading_2": {"rich_text": []}},
        {"type": "quote", "quote": {"rich_text": []}},
        {"type": "image", "id": "img-2", "image": {"caption": []}},
    ]
    output = render_blocks(blocks, {"img-2": "images/example.png"}, [])

    assert "<p></p>" not in output
    assert "<h3></h3>" not in output
    assert "<blockquote></blockquote>" not in output
    assert "<figcaption></figcaption>" not in output
    assert 'src="images/example.png"' in output


def test_toggle_is_expanded_for_copy_friendly_output():
    blocks = [{
        "type": "toggle",
        "toggle": {"rich_text": [rt("추가 설명 보기")]},
        "_children": [{
            "type": "paragraph",
            "paragraph": {"rich_text": [rt("토글 안쪽 내용") ]},
            "_children": [],
        }],
    }]
    output = render_blocks(blocks, {}, [])

    assert "<details>" not in output
    assert "추가 설명 보기" in output
    assert "토글 안쪽 내용" in output


def test_duplicate_heading_slugs_are_unique():
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": [rt("반복 제목")]}},
        {"type": "heading_1", "heading_1": {"rich_text": [rt("반복 제목")]}},
    ]
    headings = []
    output = render_blocks(blocks, {}, [], headings=headings)

    assert 'id="반복-제목"' in output
    assert 'id="반복-제목-2"' in output


def test_code_image_is_created_with_korean_text(tmp_path):
    from content_converter import render_code_image, _code_font

    destination = render_code_image(
        "// 한글 주석\nint main() { return 0; }", "cpp", tmp_path / "korean-code.png"
    )

    assert destination.exists()
    assert destination.stat().st_size > 1000
    font = _code_font(16)
    assert font.getbbox("한글 주석")[2] > 0


def test_code_image_overwrites_old_generated_file(tmp_path):
    from content_converter import render_code_image

    destination = tmp_path / "old-code.png"
    destination.write_bytes(b"old image with missing Korean glyphs")
    render_code_image("// 새 한글 주석", "cpp", destination)

    assert destination.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

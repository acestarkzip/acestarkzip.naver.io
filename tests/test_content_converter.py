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
    assert '<h2 style=' in html and '제목 1</h2>' in html
    assert '<h3 style=' in html and '제목 2</h3>' in html
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
    assert "<table style=" in html and "<th style=" in html
    assert "이름</th>" in html and "로봇</td>" in html


def test_image_map():
    block = {"id": "img-1", "type": "image", "image": {"caption": [rt("테스트 이미지")]}, "_children": []}
    html = render_blocks([block], {"img-1": "images/test.png"}, [])
    assert 'src="images/test.png"' in html
    assert "테스트 이미지" in html


def test_wrap_html():
    output = wrap_html("테스트", "<p>본문</p>", ["로봇"])
    assert "<title>테스트</title>" in output
    assert "#로봇" in output and "<p>본문</p>" in output
    assert 'id="copyTitleButton"' in output
    assert 'id="copyBodyButton"' in output


def test_body_headings_are_demoted_and_toc_is_generated():
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": [rt("📚 목차")]}},
        {"type": "heading_1", "heading_1": {"rich_text": [rt("오늘의 진행 내용")]}},
        {"type": "heading_2", "heading_2": {"rich_text": [rt("세부 주제")]}},
        {"type": "heading_3", "heading_3": {"rich_text": [rt("추가 설명")]}},
    ]
    headings = []
    body = render_blocks(blocks, {}, [], headings=headings)
    output = wrap_html("테스트 글", body, [], headings)

    assert output.count("<h1") == 1  # Preview title; body headings start at H2.
    assert '<h2 style=' in output and '오늘의 진행 내용</h2>' in output
    assert '<h3 style=' in output and '세부 주제</h3>' in output
    assert '<h4 style=' in output and '추가 설명</h4>' in output
    assert "📚 목차" in output and "오늘의 진행 내용</li>" in output and "<ul style=\"list-style-type:disc" in output
    assert '<a href="#' not in output


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

    assert len(headings) == 2
    assert headings[0]["id"] == "반복-제목"
    assert headings[1]["id"] == "반복-제목-2"


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


def test_heading_4_is_simplified_to_h4_and_in_plain_toc():
    blocks = [{
        "type": "heading_4",
        "heading_4": {"rich_text": [rt("a. 더더 깊은 세부 내용")]},
        "_children": [],
    }]
    headings = []
    body = render_blocks(blocks, {}, [], headings=headings)
    output = wrap_html("테스트", body, [], headings)

    assert '<h4 style=' in output and 'a. 더더 깊은 세부 내용</h4>' in output
    assert 'a. 더더 깊은 세부 내용' in output


def test_bookmark_caption_is_preserved_instead_of_generic_type_name():
    block = {
        "type": "bookmark",
        "bookmark": {
            "url": "https://www.youtube.com/@sssrobotics/shorts",
            "caption": [rt("S급 로봇 연구소 | SSS Robotics")],
        },
        "_children": [],
    }
    output = render_blocks([block], {}, [])

    assert "S급 로봇 연구소 | SSS Robotics" in output
    assert ">bookmark</a>" not in output


def test_contact_heading_stays_in_body_but_is_excluded_from_toc():
    blocks = [{
        "type": "heading_1",
        "heading_1": {"rich_text": [rt("📨 Contact us")]},
        "_children": [],
    }]
    headings = []
    body = render_blocks(blocks, {}, [], headings=headings)
    output = wrap_html("테스트", body, [], headings)

    assert "Contact us" in body
    assert '<h2 style=' in body
    assert 'Contact us</li>' not in output


def test_toggle_children_remain_nested_under_parent_title():
    blocks = [{
        "type": "toggle",
        "toggle": {"rich_text": [rt("상위 토글")]},
        "_children": [{
            "type": "toggle",
            "toggle": {"rich_text": [rt("하위 토글")]},
            "_children": [{
                "type": "paragraph",
                "paragraph": {"rich_text": [rt("하위 내용")]},
                "_children": [],
            }],
        }],
    }]
    output = render_blocks(blocks, {}, [])

    assert output.index("상위 토글") < output.index("하위 토글") < output.index("하위 내용")
    assert output.count("<blockquote") == 2
    assert "<details>" not in output
    assert 'class="toggle-' not in output


def test_rich_text_newlines_become_html_line_breaks():
    output = rich_text([rt("첫 번째 줄\n두 번째 줄")])
    assert "첫 번째 줄<br>\n두 번째 줄" in output


def test_html_has_separate_title_and_body_copy_targets():
    output = wrap_html("제목", "<p>본문입니다.</p>", ["로봇"])
    assert 'id="naverTitle">제목</h1>' in output
    assert 'id="naverBody"' in output
    body_section = output.split('<article id="naverBody">', 1)[1].split('</article>', 1)[0]
    assert "제목" not in body_section
    assert "본문입니다." in body_section
    assert "#로봇" in body_section
    assert "메모" not in body_section


def test_copy_friendly_output_uses_common_html_only():
    blocks = [{
        "type": "callout",
        "callout": {"rich_text": [rt("도입 내용")], "icon": {"type": "emoji", "emoji": "📌"}},
        "_children": [],
    }]
    output = render_blocks(blocks, {}, [])
    assert "<blockquote" in output
    assert "<aside" not in output
    assert 'class="callout"' not in output
    assert "도입 내용" in output

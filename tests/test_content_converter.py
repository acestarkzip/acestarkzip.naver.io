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
    assert "<h1>제목 1</h1>" in html
    assert "<h2>제목 2</h2>" in html
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

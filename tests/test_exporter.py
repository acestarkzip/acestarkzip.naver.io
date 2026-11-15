import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from exporter import date_window


def test_date_window_is_today_only():
    assert date_window(date(2026, 11, 15)) == (date(2026, 11, 15), date(2026, 11, 15))


def test_date_window_is_today_only_at_month_and_year_boundaries():
    assert date_window(date(2027, 1, 1)) == (date(2027, 1, 1), date(2027, 1, 1))



def test_visual_asset_events_keep_only_original_images_and_code_in_document_order():
    from exporter import ordered_visual_events
    blocks = [
        {"id": "img1", "type": "image", "image": {}},
        {"id": "code1", "type": "code", "code": {"rich_text": []}},
        {"id": "contact", "type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "📨 Contact us"}]}},
        {"id": "img2", "type": "image", "image": {}},
    ]
    events = [(kind, block.get("id")) for kind, block in ordered_visual_events(blocks)]
    assert events == [("image", "img1"), ("code", "code1"), ("image", "img2")]


def test_create_image_bundle_contains_all_assets_in_number_order(tmp_path):
    import zipfile
    from exporter import create_image_bundle

    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "이미지_02_코드.png").write_bytes(b"code")
    (tmp_path / "images" / "이미지_01_대표.png").write_bytes(b"banner")
    manifest = [
        {"number": 2, "path": "images/이미지_02_코드.png", "label": "코드"},
        {"number": 1, "path": "images/이미지_01_대표.png", "label": "대표 이미지"},
    ]
    archive_path = create_image_bundle(tmp_path, manifest)
    assert archive_path is not None and archive_path.exists()
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert names[:2] == ["이미지_01_대표.png", "이미지_02_코드.png"]
        assert "이미지_업로드_순서.txt" in names


def test_create_image_bundle_removes_stale_archive_when_empty(tmp_path):
    from exporter import create_image_bundle
    archive_path = tmp_path / "07_이미지_전체.zip"
    archive_path.write_bytes(b"stale")
    assert create_image_bundle(tmp_path, []) is None
    assert not archive_path.exists()


def test_raster_file_block_detects_image_by_original_filename():
    from exporter import _is_raster_media_block

    block = {
        "type": "file",
        "file": {
            "name": "robot-diagram.png",
            "external": {"url": "https://cdn.example.test/download/opaque-id"},
        },
    }
    assert _is_raster_media_block(block)


def test_save_media_uses_order_number_and_original_name_extension(tmp_path, monkeypatch):
    import exporter

    class FakeResponse:
        headers = {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            yield b"PNG!"

    monkeypatch.setattr(exporter.requests, "get", lambda *args, **kwargs: FakeResponse())
    block = {
        "type": "file",
        "file": {
            "name": "robot-diagram.png",
            "external": {"url": "https://cdn.example.test/download/opaque-id", "mime_type": ""},
        },
    }
    saved = exporter.save_media(block, tmp_path / "images", 7)
    assert saved is not None
    assert saved.name == "이미지_07_robot-diagram.png"
    assert saved.read_bytes() == b"PNG!"


def test_post_body_does_not_append_database_property_tags():
    from exporter import build_post_body_text

    body = "본문 내용\n\n#S급로봇연구소 #OpenUSD"
    result = build_post_body_text(body)

    assert result == body + "\n"
    assert "#자기개발" not in result
    assert "#S급로봇연구소" in result  # hashtags explicitly written in Notion remain intact

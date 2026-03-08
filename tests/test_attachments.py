"""
Tests for the multimodal attachment pipeline.

Covers:
  - Attachment / AttachmentType dataclass and enum
  - InboundMessage serialisation roundtrip (asdict ↔ __post_init__)
  - make_attachment() factory
  - infer_attachment_type() helper
  - attachments_to_content_blocks() LangChain conversion
"""

from __future__ import annotations

import base64
import tempfile
from dataclasses import asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Attachment dataclass
# ---------------------------------------------------------------------------


class TestAttachmentDataclass:
    def test_creation(self):
        from langclaw.bus.base import Attachment, AttachmentType

        att = Attachment(type=AttachmentType.IMAGE, mime_type="image/jpeg", data="abc123")
        assert att.type == AttachmentType.IMAGE
        assert att.mime_type == "image/jpeg"
        assert att.data == "abc123"

    def test_defaults(self):
        from langclaw.bus.base import Attachment, AttachmentType

        att = Attachment(type=AttachmentType.FILE)
        assert att.mime_type == ""
        assert att.filename == ""
        assert att.url == ""
        assert att.data == ""
        assert att.size == 0

    def test_attachment_type_is_str(self):
        from langclaw.bus.base import AttachmentType

        assert isinstance(AttachmentType.IMAGE, str)
        assert AttachmentType.IMAGE == "image"
        assert AttachmentType("audio") == AttachmentType.AUDIO


# ---------------------------------------------------------------------------
# InboundMessage serialisation roundtrip
# ---------------------------------------------------------------------------


class TestInboundMessageSerialization:
    def test_roundtrip_with_attachments(self):
        from langclaw.bus.base import Attachment, AttachmentType, InboundMessage

        msg = InboundMessage(
            channel="test",
            user_id="u1",
            context_id="c1",
            content="hi",
            attachments=[
                Attachment(type=AttachmentType.IMAGE, mime_type="image/png", data="b64data"),
            ],
        )
        raw = asdict(msg)
        restored = InboundMessage(**raw)

        assert len(restored.attachments) == 1
        assert isinstance(restored.attachments[0], Attachment)
        assert restored.attachments[0].type == AttachmentType.IMAGE
        assert restored.attachments[0].data == "b64data"

    def test_roundtrip_empty_attachments(self):
        from langclaw.bus.base import InboundMessage

        msg = InboundMessage(channel="test", user_id="u1", context_id="c1", content="hi")
        raw = asdict(msg)
        restored = InboundMessage(**raw)
        assert restored.attachments == []

    def test_roundtrip_multiple_attachments(self):
        from langclaw.bus.base import Attachment, AttachmentType, InboundMessage

        msg = InboundMessage(
            channel="test",
            user_id="u1",
            context_id="c1",
            content="look",
            attachments=[
                Attachment(type=AttachmentType.IMAGE, mime_type="image/jpeg", data="img"),
                Attachment(
                    type=AttachmentType.FILE,
                    mime_type="application/pdf",
                    data="pdf",
                    filename="report.pdf",
                ),
            ],
        )
        raw = asdict(msg)
        restored = InboundMessage(**raw)

        assert len(restored.attachments) == 2
        assert restored.attachments[0].type == AttachmentType.IMAGE
        assert restored.attachments[1].type == AttachmentType.FILE
        assert restored.attachments[1].filename == "report.pdf"


# ---------------------------------------------------------------------------
# infer_attachment_type
# ---------------------------------------------------------------------------


class TestInferAttachmentType:
    def test_image(self):
        from langclaw.bus.base import AttachmentType
        from langclaw.gateway.utils import infer_attachment_type

        assert infer_attachment_type("image/jpeg") == AttachmentType.IMAGE
        assert infer_attachment_type("image/png") == AttachmentType.IMAGE

    def test_audio(self):
        from langclaw.bus.base import AttachmentType
        from langclaw.gateway.utils import infer_attachment_type

        assert infer_attachment_type("audio/ogg") == AttachmentType.AUDIO
        assert infer_attachment_type("audio/mpeg") == AttachmentType.AUDIO

    def test_video(self):
        from langclaw.bus.base import AttachmentType
        from langclaw.gateway.utils import infer_attachment_type

        assert infer_attachment_type("video/mp4") == AttachmentType.VIDEO

    def test_fallback_to_file(self):
        from langclaw.bus.base import AttachmentType
        from langclaw.gateway.utils import infer_attachment_type

        assert infer_attachment_type("application/pdf") == AttachmentType.FILE
        assert infer_attachment_type("text/plain") == AttachmentType.FILE
        assert infer_attachment_type("") == AttachmentType.FILE


# ---------------------------------------------------------------------------
# make_attachment
# ---------------------------------------------------------------------------


class TestMakeAttachment:
    def test_from_url(self):
        from langclaw.bus.base import AttachmentType
        from langclaw.gateway.utils import make_attachment

        att = make_attachment(url="https://example.com/photo.jpg", filename="photo.jpg")
        assert att.type == AttachmentType.IMAGE
        assert att.url == "https://example.com/photo.jpg"
        assert att.mime_type == "image/jpeg"

    def test_from_data(self):
        from langclaw.bus.base import AttachmentType
        from langclaw.gateway.utils import make_attachment

        att = make_attachment(data="abc", mime_type="audio/ogg", filename="voice.ogg")
        assert att.type == AttachmentType.AUDIO
        assert att.data == "abc"

    def test_from_file_path(self):
        from langclaw.bus.base import AttachmentType
        from langclaw.gateway.utils import make_attachment

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"hello world")
            tmp_path = f.name

        try:
            att = make_attachment(file_path=tmp_path)
            assert att.type == AttachmentType.FILE
            assert att.filename == Path(tmp_path).name
            assert att.data == base64.b64encode(b"hello world").decode("ascii")
            assert att.size == 11
            assert "text/" in att.mime_type
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_type_override(self):
        from langclaw.bus.base import AttachmentType
        from langclaw.gateway.utils import make_attachment

        att = make_attachment(
            data="abc",
            mime_type="application/octet-stream",
            attachment_type=AttachmentType.VIDEO,
        )
        assert att.type == AttachmentType.VIDEO

    def test_mime_inferred_from_filename(self):
        from langclaw.gateway.utils import make_attachment

        att = make_attachment(data="abc", filename="song.mp3")
        assert att.mime_type == "audio/mpeg"

    def test_no_mime_no_filename(self):
        from langclaw.bus.base import AttachmentType
        from langclaw.gateway.utils import make_attachment

        att = make_attachment(data="abc")
        assert att.type == AttachmentType.FILE
        assert att.mime_type == ""


# ---------------------------------------------------------------------------
# attachments_to_content_blocks
# ---------------------------------------------------------------------------


class TestAttachmentsToContentBlocks:
    def test_no_attachments_returns_string(self):
        from langclaw.gateway.utils import attachments_to_content_blocks

        result = attachments_to_content_blocks("hello", [])
        assert result == "hello"
        assert isinstance(result, str)

    def test_image_with_base64(self):
        from langclaw.bus.base import Attachment, AttachmentType
        from langclaw.gateway.utils import attachments_to_content_blocks

        att = Attachment(type=AttachmentType.IMAGE, mime_type="image/jpeg", data="abc123")
        result = attachments_to_content_blocks("describe this", [att])

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "describe this"}
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"] == "data:image/jpeg;base64,abc123"

    def test_image_with_url(self):
        from langclaw.bus.base import Attachment, AttachmentType
        from langclaw.gateway.utils import attachments_to_content_blocks

        att = Attachment(type=AttachmentType.IMAGE, url="https://example.com/img.jpg")
        result = attachments_to_content_blocks("", [att])

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"] == "https://example.com/img.jpg"

    def test_file_attachment_base64(self):
        from langclaw.bus.base import Attachment, AttachmentType
        from langclaw.gateway.utils import attachments_to_content_blocks

        att = Attachment(
            type=AttachmentType.FILE,
            mime_type="application/pdf",
            data="pdfdata",
            filename="report.pdf",
        )
        result = attachments_to_content_blocks("review this", [att])

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "review this"}
        assert result[1]["type"] == "file"
        assert result[1]["filename"] == "report.pdf"
        assert result[1]["source"]["type"] == "base64"
        assert result[1]["source"]["data"] == "pdfdata"

    def test_file_attachment_url(self):
        from langclaw.bus.base import Attachment, AttachmentType
        from langclaw.gateway.utils import attachments_to_content_blocks

        att = Attachment(
            type=AttachmentType.FILE,
            url="https://example.com/doc.pdf",
            filename="doc.pdf",
        )
        result = attachments_to_content_blocks("", [att])

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "file"
        assert result[0]["source"]["type"] == "url"
        assert result[0]["source"]["url"] == "https://example.com/doc.pdf"

    def test_audio_attachment(self):
        from langclaw.bus.base import Attachment, AttachmentType
        from langclaw.gateway.utils import attachments_to_content_blocks

        att = Attachment(
            type=AttachmentType.AUDIO,
            mime_type="audio/ogg",
            data="audiodata",
            filename="voice.ogg",
        )
        result = attachments_to_content_blocks("", [att])

        assert isinstance(result, list)
        assert result[0]["type"] == "file"
        assert result[0]["source"]["media_type"] == "audio/ogg"

    def test_mixed_attachments(self):
        from langclaw.bus.base import Attachment, AttachmentType
        from langclaw.gateway.utils import attachments_to_content_blocks

        atts = [
            Attachment(type=AttachmentType.IMAGE, mime_type="image/png", data="imgdata"),
            Attachment(
                type=AttachmentType.FILE,
                mime_type="text/plain",
                data="txtdata",
                filename="notes.txt",
            ),
        ]
        result = attachments_to_content_blocks("check both", atts)

        assert isinstance(result, list)
        assert len(result) == 3  # text + image + file
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image_url"
        assert result[2]["type"] == "file"

    def test_empty_text_no_text_block(self):
        from langclaw.bus.base import Attachment, AttachmentType
        from langclaw.gateway.utils import attachments_to_content_blocks

        att = Attachment(type=AttachmentType.IMAGE, mime_type="image/jpeg", data="abc")
        result = attachments_to_content_blocks("", [att])

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "image_url"

    def test_attachment_no_url_no_data_skipped(self):
        from langclaw.bus.base import Attachment, AttachmentType
        from langclaw.gateway.utils import attachments_to_content_blocks

        att = Attachment(type=AttachmentType.IMAGE, mime_type="image/jpeg")
        result = attachments_to_content_blocks("hello", [att])

        # Only text block, image was skipped
        assert result == "hello"

    def test_video_attachment(self):
        from langclaw.bus.base import Attachment, AttachmentType
        from langclaw.gateway.utils import attachments_to_content_blocks

        att = Attachment(
            type=AttachmentType.VIDEO,
            mime_type="video/mp4",
            data="videodata",
            filename="clip.mp4",
        )
        result = attachments_to_content_blocks("watch", [att])

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[1]["type"] == "file"
        assert result[1]["filename"] == "clip.mp4"

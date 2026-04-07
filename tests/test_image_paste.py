# ABOUTME: Tests for image paste support — content block building and bridge integration.
# ABOUTME: Verifies text+image messages flow correctly from server to ACP bridge.

import base64
from unittest.mock import AsyncMock, MagicMock

import acp
import pytest

from agent_kitchen.acp_bridge import ACPBridge


def _b64_pixel():
    """A minimal valid base64-encoded 1x1 PNG for testing."""
    # 1x1 red PNG, 67 bytes
    data = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
        b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return base64.b64encode(data).decode("ascii")


class TestBuildContentBlocks:
    """Content block assembly from WebSocket messages (mirrors server logic)."""

    def test_text_only(self):
        msg = {"type": "user_message", "text": "hello"}
        blocks = []
        text = msg.get("text", "").strip()
        if text:
            blocks.append(acp.text_block(text))
        for img in msg.get("images", []):
            blocks.append(acp.image_block(img["data"], img["mimeType"]))

        assert len(blocks) == 1
        assert blocks[0].type == "text"
        assert blocks[0].text == "hello"

    def test_text_with_image(self):
        b64 = _b64_pixel()
        msg = {
            "type": "user_message",
            "text": "what's in this image?",
            "images": [{"data": b64, "mimeType": "image/png"}],
        }
        blocks = []
        text = msg.get("text", "").strip()
        if text:
            blocks.append(acp.text_block(text))
        for img in msg.get("images", []):
            blocks.append(acp.image_block(img["data"], img["mimeType"]))

        assert len(blocks) == 2
        assert blocks[0].type == "text"
        assert blocks[0].text == "what's in this image?"
        assert blocks[1].type == "image"
        assert blocks[1].data == b64
        assert blocks[1].mime_type == "image/png"

    def test_image_only_no_text(self):
        b64 = _b64_pixel()
        msg = {
            "type": "user_message",
            "text": "",
            "images": [{"data": b64, "mimeType": "image/jpeg"}],
        }
        blocks = []
        text = msg.get("text", "").strip()
        if text:
            blocks.append(acp.text_block(text))
        for img in msg.get("images", []):
            blocks.append(acp.image_block(img["data"], img["mimeType"]))

        assert len(blocks) == 1
        assert blocks[0].type == "image"
        assert blocks[0].mime_type == "image/jpeg"

    def test_multiple_images(self):
        b64 = _b64_pixel()
        msg = {
            "type": "user_message",
            "text": "compare these",
            "images": [
                {"data": b64, "mimeType": "image/png"},
                {"data": b64, "mimeType": "image/jpeg"},
            ],
        }
        blocks = []
        text = msg.get("text", "").strip()
        if text:
            blocks.append(acp.text_block(text))
        for img in msg.get("images", []):
            blocks.append(acp.image_block(img["data"], img["mimeType"]))

        assert len(blocks) == 3
        assert blocks[0].type == "text"
        assert blocks[1].type == "image"
        assert blocks[1].mime_type == "image/png"
        assert blocks[2].type == "image"
        assert blocks[2].mime_type == "image/jpeg"

    def test_no_text_no_images_produces_empty(self):
        msg = {"type": "user_message", "text": "  ", "images": []}
        blocks = []
        text = msg.get("text", "").strip()
        if text:
            blocks.append(acp.text_block(text))
        for img in msg.get("images", []):
            blocks.append(acp.image_block(img["data"], img["mimeType"]))

        assert len(blocks) == 0

    def test_missing_images_key_treated_as_empty(self):
        msg = {"type": "user_message", "text": "just text"}
        blocks = []
        text = msg.get("text", "").strip()
        if text:
            blocks.append(acp.text_block(text))
        for img in msg.get("images", []):
            blocks.append(acp.image_block(img["data"], img["mimeType"]))

        assert len(blocks) == 1
        assert blocks[0].type == "text"


class TestBridgePromptWithImages:
    """Integration: bridge.prompt() forwards image blocks to ACP connection."""

    @pytest.mark.asyncio
    async def test_image_blocks_reach_connection(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        bridge._session_id = "img-session"
        mock_response = MagicMock()
        mock_response.stopReason = "end_turn"
        bridge._conn = AsyncMock()
        bridge._conn.prompt = AsyncMock(return_value=mock_response)
        bridge._proc = MagicMock(returncode=None)

        b64 = _b64_pixel()
        blocks = [
            acp.text_block("describe this"),
            acp.image_block(b64, "image/png"),
        ]
        result = await bridge.prompt(blocks)

        assert result == "end_turn"
        call_args = bridge._conn.prompt.call_args
        assert call_args.kwargs["prompt"] == blocks
        assert len(call_args.kwargs["prompt"]) == 2
        assert call_args.kwargs["prompt"][1].type == "image"

    @pytest.mark.asyncio
    async def test_image_only_prompt(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        bridge._session_id = "img-only-session"
        mock_response = MagicMock()
        mock_response.stopReason = "end_turn"
        bridge._conn = AsyncMock()
        bridge._conn.prompt = AsyncMock(return_value=mock_response)
        bridge._proc = MagicMock(returncode=None)

        b64 = _b64_pixel()
        blocks = [acp.image_block(b64, "image/png")]
        result = await bridge.prompt(blocks)

        assert result == "end_turn"
        sent_blocks = bridge._conn.prompt.call_args.kwargs["prompt"]
        assert len(sent_blocks) == 1
        assert sent_blocks[0].type == "image"
        assert sent_blocks[0].data == b64

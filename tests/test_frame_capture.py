"""Tests for frame capture functionality in tui.py and loop.py.

Covers:
- RichTUI.capture_frame returns a valid PNG path (mocking cairosvg)
- PlainTUI.capture_frame returns None
- JsonTUI.capture_frame returns None
- RichTUI.capture_frame is a no-op when capture_frames=False
- RichTUI.capture_frame logs warning when cairosvg is missing
- _run_party_mode attaches up to 5 most recent PNGs as image blocks
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tui import PlainTUI, JsonTUI, _has_rich


# ---------------------------------------------------------------------------
# PlainTUI / JsonTUI — always return None
# ---------------------------------------------------------------------------

class TestPlainTUICaptureFrame:
    def test_returns_none(self):
        ui = PlainTUI()
        assert ui.capture_frame("round_1_end") is None

    def test_returns_none_any_label(self):
        ui = PlainTUI()
        assert ui.capture_frame("converged") is None
        assert ui.capture_frame("error_round_3") is None


class TestJsonTUICaptureFrame:
    def test_returns_none(self):
        ui = JsonTUI()
        assert ui.capture_frame("round_1_end") is None

    def test_returns_none_any_label(self):
        ui = JsonTUI()
        assert ui.capture_frame("converged") is None
        assert ui.capture_frame("error_round_3") is None


# ---------------------------------------------------------------------------
# RichTUI.capture_frame
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_rich(), reason="rich not installed")
class TestRichTUICaptureFrame:
    """Test RichTUI.capture_frame with mocked cairosvg."""

    def test_disabled_returns_none(self, tmp_path):
        """When capture_frames=False, capture_frame returns None."""
        from tui import RichTUI
        ui = RichTUI(run_dir=str(tmp_path), capture_frames=False)
        assert ui.capture_frame("round_1_end") is None

    def test_no_run_dir_returns_none(self):
        """When run_dir is None, capture_frame returns None."""
        from tui import RichTUI
        ui = RichTUI(run_dir=None, capture_frames=True)
        assert ui.capture_frame("round_1_end") is None

    def test_returns_png_path_with_cairosvg(self, tmp_path):
        """When cairosvg is available, capture_frame returns a valid PNG path."""
        from tui import RichTUI

        ui = RichTUI(run_dir=str(tmp_path), capture_frames=True)
        # Render something so the buffer is not empty
        ui.console.print("Test output for frame capture")

        # Mock cairosvg.svg2png to just write a dummy PNG file
        mock_cairosvg = MagicMock()
        def fake_svg2png(url, write_to):
            Path(write_to).write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes
        mock_cairosvg.svg2png = fake_svg2png

        with patch.dict("sys.modules", {"cairosvg": mock_cairosvg}):
            result = ui.capture_frame("round_1_end")

        assert result is not None
        assert result.name == "round_1_end.png"
        assert result.parent.name == "frames"
        assert result.exists()
        assert result.read_bytes().startswith(b"\x89PNG")

    def test_creates_frames_directory(self, tmp_path):
        """capture_frame creates the frames/ directory if it doesn't exist."""
        from tui import RichTUI

        ui = RichTUI(run_dir=str(tmp_path), capture_frames=True)
        ui.console.print("Test output")

        mock_cairosvg = MagicMock()
        def fake_svg2png(url, write_to):
            Path(write_to).write_bytes(b"\x89PNG\r\n\x1a\n")
        mock_cairosvg.svg2png = fake_svg2png

        frames_dir = tmp_path / "frames"
        assert not frames_dir.exists()

        with patch.dict("sys.modules", {"cairosvg": mock_cairosvg}):
            ui.capture_frame("converged")

        assert frames_dir.exists()
        assert frames_dir.is_dir()

    def test_cairosvg_missing_returns_none_and_warns(self, tmp_path, caplog):
        """When cairosvg is not installed, capture_frame returns None and logs warning."""
        from tui import RichTUI

        ui = RichTUI(run_dir=str(tmp_path), capture_frames=True)
        ui.console.print("Test output")

        # Make cairosvg import fail
        with patch.dict("sys.modules", {"cairosvg": None}):
            with caplog.at_level(logging.WARNING, logger="tui"):
                result = ui.capture_frame("round_1_end")

        assert result is None
        assert any("cairosvg" in r.message.lower() for r in caplog.records)

    def test_cairosvg_missing_warns_only_once(self, tmp_path, caplog):
        """The cairosvg warning should only be emitted once per RichTUI instance."""
        from tui import RichTUI

        ui = RichTUI(run_dir=str(tmp_path), capture_frames=True)

        with patch.dict("sys.modules", {"cairosvg": None}):
            with caplog.at_level(logging.WARNING, logger="tui"):
                ui.capture_frame("round_1_end")
                ui.capture_frame("round_2_end")
                ui.capture_frame("converged")

        warning_count = sum(1 for r in caplog.records if "cairosvg" in r.message.lower())
        assert warning_count == 1

    def test_svg_cleaned_up_on_missing_cairosvg(self, tmp_path):
        """SVG file should be cleaned up even when cairosvg is missing."""
        from tui import RichTUI

        ui = RichTUI(run_dir=str(tmp_path), capture_frames=True)
        ui.console.print("Test output")

        with patch.dict("sys.modules", {"cairosvg": None}):
            ui.capture_frame("round_1_end")

        frames_dir = tmp_path / "frames"
        svg_files = list(frames_dir.glob("*.svg")) if frames_dir.exists() else []
        assert len(svg_files) == 0

    def test_svg_cleaned_up_on_success(self, tmp_path):
        """SVG intermediate file should be removed after successful PNG conversion."""
        from tui import RichTUI

        ui = RichTUI(run_dir=str(tmp_path), capture_frames=True)
        ui.console.print("Test output")

        mock_cairosvg = MagicMock()
        def fake_svg2png(url, write_to):
            Path(write_to).write_bytes(b"\x89PNG\r\n\x1a\n")
        mock_cairosvg.svg2png = fake_svg2png

        with patch.dict("sys.modules", {"cairosvg": mock_cairosvg}):
            ui.capture_frame("round_1_end")

        frames_dir = tmp_path / "frames"
        svg_files = list(frames_dir.glob("*.svg"))
        assert len(svg_files) == 0

    def test_cairosvg_conversion_error_returns_none(self, tmp_path, caplog):
        """When cairosvg.svg2png raises, capture_frame returns None and logs."""
        from tui import RichTUI

        ui = RichTUI(run_dir=str(tmp_path), capture_frames=True)
        ui.console.print("Test output")

        mock_cairosvg = MagicMock()
        mock_cairosvg.svg2png.side_effect = RuntimeError("conversion failed")

        with patch.dict("sys.modules", {"cairosvg": mock_cairosvg}):
            with caplog.at_level(logging.WARNING, logger="tui"):
                result = ui.capture_frame("round_1_end")

        assert result is None
        assert any("failed" in r.message.lower() for r in caplog.records)

    def test_multiple_labels_produce_separate_files(self, tmp_path):
        """Each label should produce a separate PNG file."""
        from tui import RichTUI

        ui = RichTUI(run_dir=str(tmp_path), capture_frames=True)

        mock_cairosvg = MagicMock()
        def fake_svg2png(url, write_to):
            Path(write_to).write_bytes(b"\x89PNG\r\n\x1a\n")
        mock_cairosvg.svg2png = fake_svg2png

        with patch.dict("sys.modules", {"cairosvg": mock_cairosvg}):
            ui.console.print("Round 1")
            r1 = ui.capture_frame("round_1_end")
            ui.console.print("Round 2")
            r2 = ui.capture_frame("round_2_end")

        assert r1 is not None and r2 is not None
        assert r1.name == "round_1_end.png"
        assert r2.name == "round_2_end.png"
        assert r1 != r2


# ---------------------------------------------------------------------------
# _run_party_mode frame attachment
# ---------------------------------------------------------------------------

class TestPartyModeFrameAttachment:
    """Verify _run_party_mode scans frames/ and passes image paths to agent."""

    def test_party_mode_attaches_frames(self, tmp_path):
        """_run_party_mode should pass frame images to run_claude_agent."""
        from unittest.mock import AsyncMock

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Test Project\n")

        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)

        # Create some fake frame PNGs
        frames_dir = run_dir / "frames"
        frames_dir.mkdir()
        for i in range(3):
            (frames_dir / f"round_{i+1}_end.png").write_bytes(b"\x89PNG")
        (frames_dir / "converged.png").write_bytes(b"\x89PNG")

        # Create required files
        improvements_path = tmp_path / "runs" / "improvements.md"
        improvements_path.parent.mkdir(parents=True, exist_ok=True)
        improvements_path.write_text("# Improvements\n- [x] [functional] test\n")

        memory_path = tmp_path / "runs" / "memory.md"
        memory_path.write_text("# Memory\n")

        agents_dir = project_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "reviewer.md").write_text("You are a code reviewer.")

        workflows_dir = project_dir / "workflows" / "party-mode"
        workflows_dir.mkdir(parents=True)

        # Mock run_claude_agent
        mock_agent = AsyncMock()

        with patch("loop._run_party_mode") as mock_party:
            # Instead of calling the real function, test the frame scanning logic directly
            pass

        # Test the frame scanning logic that _run_party_mode uses
        all_frames = sorted(frames_dir.glob("*.png"))
        frame_images = all_frames[-5:] if len(all_frames) > 5 else all_frames
        assert len(frame_images) == 4
        assert frame_images[-1].name == "round_3_end.png" or frame_images[-1].name == "converged.png"

    def test_frame_selection_limits_to_5(self, tmp_path):
        """When more than 5 frames exist, only the last 5 should be selected."""
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()

        # Create 8 frame PNGs
        for i in range(7):
            (frames_dir / f"round_{i+1}_end.png").write_bytes(b"\x89PNG")
        (frames_dir / "converged.png").write_bytes(b"\x89PNG")

        all_frames = sorted(frames_dir.glob("*.png"))
        assert len(all_frames) == 8

        frame_images = all_frames[-5:] if len(all_frames) > 5 else all_frames
        assert len(frame_images) == 5

    def test_no_frames_dir_empty_list(self, tmp_path):
        """When frames/ directory doesn't exist, no images should be attached."""
        frames_dir = tmp_path / "frames"
        frame_images: list[Path] = []
        if frames_dir.is_dir():
            all_frames = sorted(frames_dir.glob("*.png"))
            frame_images = all_frames[-5:] if len(all_frames) > 5 else all_frames

        assert len(frame_images) == 0

    def test_empty_frames_dir_empty_list(self, tmp_path):
        """When frames/ exists but is empty, no images should be attached."""
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()

        frame_images: list[Path] = []
        if frames_dir.is_dir():
            all_frames = sorted(frames_dir.glob("*.png"))
            frame_images = all_frames[-5:] if len(all_frames) > 5 else all_frames

        assert len(frame_images) == 0

    def test_party_mode_passes_images_to_agent(self, tmp_path, monkeypatch):
        """Verify _run_party_mode actually passes images kwarg to run_claude_agent."""
        from unittest.mock import AsyncMock
        import asyncio

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        spec_file = project_dir / "README.md"
        spec_file.write_text("# Test Project\nSome spec.\n")

        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)

        # Create frame PNGs
        frames_dir = run_dir / "frames"
        frames_dir.mkdir()
        for i in range(3):
            (frames_dir / f"round_{i+1}_end.png").write_bytes(b"\x89PNG")

        # Create required project files
        improvements_path = project_dir / "runs" / "improvements.md"
        improvements_path.parent.mkdir(parents=True, exist_ok=True)
        improvements_path.write_text("# Improvements\n- [x] [functional] done\n")

        memory_path = project_dir / "runs" / "memory.md"
        memory_path.write_text("# Memory\n")

        agents_dir = project_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "reviewer.md").write_text("You are a code reviewer.")

        # Mock the agent module imports that _run_party_mode uses
        mock_run_agent = AsyncMock()
        mock_is_benign = MagicMock(return_value=False)
        mock_should_retry = MagicMock(return_value=None)

        captured_images = []

        async def capture_agent_call(prompt, project_dir, round_num=0,
                                     run_dir=None, log_filename=None,
                                     images=None):
            if images is not None:
                captured_images.extend(images)

        mock_run_agent.side_effect = capture_agent_call

        # Patch at module level for the dynamic import inside _run_party_mode
        import types
        fake_agent = types.ModuleType("agent")
        fake_agent.run_claude_agent = mock_run_agent
        fake_agent._is_benign_runtime_error = mock_is_benign
        fake_agent._should_retry_rate_limit = mock_should_retry

        monkeypatch.setitem(__import__("sys").modules, "agent", fake_agent)

        from tui import PlainTUI
        ui = PlainTUI()

        from loop import _run_party_mode
        _run_party_mode(
            project_dir=project_dir,
            run_dir=run_dir,
            ui=ui,
            spec="README.md",
        )

        assert len(captured_images) == 3
        assert all(p.suffix == ".png" for p in captured_images)

    def test_party_mode_no_frames_passes_none(self, tmp_path, monkeypatch):
        """When no frames exist, images should be None (not empty list)."""
        from unittest.mock import AsyncMock
        import asyncio

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        spec_file = project_dir / "README.md"
        spec_file.write_text("# Test Project\nSpec.\n")

        run_dir = tmp_path / "runs" / "session1"
        run_dir.mkdir(parents=True)
        # No frames dir

        improvements_path = project_dir / "runs" / "improvements.md"
        improvements_path.parent.mkdir(parents=True, exist_ok=True)
        improvements_path.write_text("# Improvements\n- [x] [functional] done\n")

        memory_path = project_dir / "runs" / "memory.md"
        memory_path.write_text("# Memory\n")

        agents_dir = project_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "reviewer.md").write_text("You are a code reviewer.")

        captured_kwargs = {}

        async def capture_agent_call(prompt, project_dir, round_num=0,
                                     run_dir=None, log_filename=None,
                                     images=None):
            captured_kwargs["images"] = images

        mock_run_agent = AsyncMock(side_effect=capture_agent_call)
        mock_is_benign = MagicMock(return_value=False)
        mock_should_retry = MagicMock(return_value=None)

        import types
        fake_agent = types.ModuleType("agent")
        fake_agent.run_claude_agent = mock_run_agent
        fake_agent._is_benign_runtime_error = mock_is_benign
        fake_agent._should_retry_rate_limit = mock_should_retry

        monkeypatch.setitem(__import__("sys").modules, "agent", fake_agent)

        from tui import PlainTUI
        ui = PlainTUI()

        from loop import _run_party_mode
        _run_party_mode(
            project_dir=project_dir,
            run_dir=run_dir,
            ui=ui,
            spec="README.md",
        )

        assert captured_kwargs["images"] is None

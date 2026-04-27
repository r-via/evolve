# 005 — Frame Capture Design (TUI Visual Context for Party Mode)

Archived from SPEC.md § TUI on 2026-04-27.
Trigger: SPEC.md > 2000 lines (3075 lines at archival time).

---

### Frame capture (visual context for party mode)

Party mode is blind to the TUI by default — its agents only see the spec,
`improvements.md`, and `memory.md`. With frame capture enabled, evolve
snapshots the rendered TUI at key moments and hands those images to the
party-mode agents, so they can reason about layout, density, progress
visualization, and visual design drift the same way a human operator would.

**Opt-in via config.** Frame capture is off by default (adds disk I/O per
round and requires an optional dependency). Enable it in `evolve.toml`:

```toml
[tool.evolve]
capture_frames = true
```

Or via CLI: `--capture-frames` / env var `EVOLVE_CAPTURE_FRAMES=1`.

**How it works.**

1. `RichTUI` is instantiated with `Console(record=True)`, which accumulates the
   rendered output in an internal buffer without extra overhead
2. The `TUIProtocol` exposes a `capture_frame(label: str) -> Path | None` method:
   - `RichTUI` exports the buffer to SVG via `console.save_svg()` (built-in,
     no new dep), then converts the SVG to PNG via `cairosvg`
   - `PlainTUI` and `JsonTUI` return `None` — there is no visual to capture
3. Captured PNGs land in `runs/<session>/frames/` with deterministic names:
   - `round_N_end.png` — after every completed round
   - `converged.png` — at convergence, just before party mode
   - `error_round_N.png` — on crash / stall / zero-progress
4. Party mode (`_run_party_mode`) scans `frames/` and picks the last 3-5 images
   (convergence + the two or three rounds before it). These are attached to
   each agent's prompt as image blocks via the Claude Agent SDK's multimodal
   input format:

   ```python
   messages = [{
       "role": "user",
       "content": [
           {"type": "text", "text": prompt_text},
           {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ...}},
           ...
       ],
   }]
   ```

5. The agents can now cite visual evidence in `party_report.md` ("the round
   header's progress bar was clipped at 80 cols", "the completion summary
   buries the improvement count below the fold") and propose concrete visual
   fixes in the spec proposal.

**Bonus — visual evolution report.** When frame capture is on,
`evolution_report.md` embeds the captured PNGs inline under a "Timeline"
section, so post-session review shows a visual progression of the run, not
just a table of commit messages.

**Dependencies.** Frame capture requires `cairosvg` (for SVG->PNG conversion).
Install with `pip install ".[vision]"`. When the `[vision]` extra is missing,
`capture_frames = true` is a no-op and evolve logs a one-line warning at
startup — the run is never blocked on a missing optional dep.

**Headless-safe.** The entire pipeline runs inside Rich's internal buffer
plus `cairosvg`; no X11, no Wayland, no real terminal screenshot. Works in CI,
Docker, and remote SSH sessions identically.

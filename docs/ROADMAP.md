# OpenCRBOT roadmap

## Milestone 0 — ADB foundation (implemented; emulator validation pending)

- Discover compatible ADB, list/connect devices, capture a PNG.
- Target GPGPC Developer Emulator at `localhost:6520`.
- No root or patcher dependency.
- `opencrbot record` writes a bounded screenshot session with dimensions, timestamps and SHA-256 manifest.
- `contracts.py` defines evidence provenance and fail-closed proposal validation; it does not dispatch actions.

**Exit condition:** Developer Emulator is listed and repeatable screenshots can be captured with serial/time/hash metadata.

## Milestone 1 — Reproducible capture/replay (capture + integrity verification implemented)

- Record bounded screenshot sessions and JSONL manifests.
- Track frame sequence, dimensions, monotonic timestamps, hashes and capture source.
- Add a replay reader so perception can run offline over the same frames.

**Exit condition:** a saved session can be replayed deterministically and inspected without a running emulator. The current milestone verifies frame order/hash/dimensions; the replay UI/reader remains pending.

## Milestone 2 — Screen calibration and perception

- Implemented an optional local YOLO detector, card-template similarity, pixel-bar elixir estimate and annotated overlay for one frame.
- Added a reference geometry config sourced from `clash-ai`'s phone profile; it is not a GPGPC profile.
- Calibrate orientation, arena crop, hand slots and HUD regions from GPGPC captures.
- Add multi-frame tracking and properly calibrated providers for elixir, cards, towers and units; each returns confidence/source/freshness.
- Keep a UI-visible unknown state when confidence is low; validate detector coverage on held-out captures.

**Exit condition:** measurements meet per-field precision/recall targets on labelled, held-out GPGPC frames; no action mode yet.

## Milestone 3 — Policy baseline and offline evaluation

- Implemented a pure, limited defensive-hint policy. It never accesses ADB and recommendations are not executable.
- Add replay evaluation and broaden rules only after the perception contract is validated.
- Re-run it over saved captures/replays; compare decisions and regressions.
- Optional adapter to a simulator only after observation/action/card versions are mapped.

**Exit condition:** paired evaluation report, model/data manifest and repeatable results.

## Milestone 4 — Supervised action transport

- Add explicit action proposals, user confirmation mode, screen-bound validation and visual post-action verification.
- Exercise on a local/offline or friendly/training scenario; stop automatically on stale frames, ambiguous selection, disconnect or unsupported build.

**Exit condition:** every action has a verified result event and rejected actions leave policy state unchanged.

## Milestone 5 — Learning and optional providers

- Add strategy tuning or compatible model serving using captured and offline data.
- Require held-out evaluation and rollbackable model manifests.
- Consider additional source providers only when their licenses, version fingerprints and capabilities are clear.

## Deliberately excluded from the baseline

- Copying unlicensed code or opaque checkpoints.
- Blind reuse of game-memory offsets across emulator/game versions.
- Treating opponent hidden hand/queue as visible state.
- Running GPGPCPatcher automatically from OpenCRBOT.

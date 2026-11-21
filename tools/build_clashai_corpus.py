"""Replay public IL_Replay events in ClashAI's simulator and write dataset_gen-compatible snapshots.

The source parquet contains actions, not observations. This tool reconstructs observations by replaying
those public actions in RoyaleSim; it never invents troop positions or opponent information.

Run from the OpenCRBOT checkout with its Python 3.12 environment. The default runtime is the
ClashAI-supported PyPI fallback (RoyaleSim 0.1.17 / RoyaleGym 0.1.18); a pinned runtime can be
selected with --runtime when its build is installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DEFAULT_CLASHAI = WORKSPACE / "ClashAI"
DEFAULT_HF = ROOT / "runs" / "clashai-replay-data" / "replays"
DEFAULT_OUT = ROOT / "runs" / "clashai-replay-data" / "sim-corpus"
RECORD_EVERY = 20
TAIL_CAP = 7200


class ReplaySkip(ValueError):
    """A public replay cannot be reconstructed without changing its action labels."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def base_key(key: str) -> str:
    key = str(key).lower()
    for suffix in ("-evolution", "-ev1", "-hero"):
        while key.endswith(suffix):
            key = key[: -len(suffix)]
    return key


def deck_form(key: str) -> str:
    key = str(key).lower()
    if key.endswith(("-evolution", "-ev1")):
        return "evolution"
    if key.endswith("-hero"):
        return "hero"
    return "base"


def world_xy(event: dict[str, Any]) -> tuple[int, int]:
    fields = event.get("source_fields") or {}
    x, y = fields.get("data_x"), fields.get("data_y")
    if x is None or y is None:
        raise ReplaySkip("unpositioned play")
    x, y = int(x), int(y)
    # IL_Replay's source_fields coordinates are relative to the acting seat when data_i == 1.
    if int(fields.get("data_i") or 0) == 1:
        x, y = 18000 - x, 32000 - y
    if not (0 <= x <= 18000 and 0 <= y <= 32000):
        raise ReplaySkip("coordinate outside arena")
    return x, y


def event_tick(event: dict[str, Any]) -> int:
    fields = event.get("source_fields") or {}
    return int(event.get("replay_tick_20hz", fields.get("data_t", 0)))


def make_match(payload: dict[str, Any], env, card_by_slug: dict[str, str], *, record_every: int,
               tail_cap: int) -> tuple[dict, dict]:
    from pipeline.royale_env import RESULT_CODE_NAMES

    tag = str((payload.get("source") or {}).get("replay_tag") or payload.get("replay_tag") or "")
    if not tag:
        raise ReplaySkip("missing replay tag")
    battle = payload.get("battle") or {}
    deck_items: dict[int, list[dict[str, str]]] = {}
    final_decks: dict[str, list[str]] = {}
    # Match ClashAI's own HF reader: source red/opponent -> engine side 0, blue/team -> side 1.
    for side, source_side in ((0, "opponent"), (1, "team")):
        players = (battle.get(source_side) or {}).get("players") or []
        if len(players) != 1:
            raise ReplaySkip("not one player per side")
        cards = players[0].get("deck") or []
        if len(cards) != 8:
            raise ReplaySkip("deck is not eight cards")
        items = []
        for card in cards:
            source_key = card.get("card_key", "")
            slug = base_key(source_key)
            name = card_by_slug.get(slug)
            if name is None:
                raise ReplaySkip(f"card outside simulator catalogue: {slug}")
            items.append({"slug": slug, "name": name, "form": deck_form(source_key)})
        if len({x["slug"] for x in items}) != 8:
            raise ReplaySkip("deck has duplicate base cards")
        deck_items[side] = items
        final_decks[str(side)] = [x["name"] if x["form"] == "base" else f"{x['name']}@{x['form']}"
                                  for x in items]
    source_crowns = {}
    for side, source_side in ((0, "opponent"), (1, "team")):
        crowns = (battle.get(source_side) or {}).get("crowns")
        if crowns is None:
            raise ReplaySkip(f"missing source result crowns: {source_side}")
        source_crowns[str(side)] = int(crowns)

    actions: dict[int, list[dict[str, Any]]] = {0: [], 1: []}
    abilities = 0
    events = sorted(payload.get("events") or [], key=lambda e: (event_tick(e), int(e.get("source_index", 0))))
    last_tick = [-1, -1]
    for order, event in enumerate(events):
        if event.get("kind") != "play_card":
            abilities += 1
            continue
        seat = 1 if event.get("side") == "team" else 0 if event.get("side") == "opponent" else -1
        if seat not in (0, 1):
            raise ReplaySkip("unknown event side")
        slug = base_key(event.get("card_key", ""))
        deck_pos = next((i for i, card in enumerate(deck_items[seat]) if card["slug"] == slug), None)
        if deck_pos is None:
            raise ReplaySkip(f"play outside recorded deck: {slug}")
        x, y = world_xy(event)
        tick = max(event_tick(event), int(env.warmup_ticks))
        # The replay interface offers one action per seat per engine tick; a second same-tick play
        # waits for that seat's next tick, as RoyaleImitate's replayer does.
        tick = max(tick, last_tick[seat] + 1)
        last_tick[seat] = tick
        actions[seat].append({"tick": tick, "source_tick": event_tick(event), "play_index": int(event.get("source_index", order)),
                              "side": seat, "card": slug, "deck_index": deck_pos, "x": x, "y": y})
    if not actions[0] and not actions[1]:
        raise ReplaySkip("no positioned card plays")

    entry = {
        "tag": tag, "icebow_side": 0, "ghost_side": 1,
        "icebow_deck": deck_items[0], "ghost_deck": deck_items[1],
        "icebow_commands": [{k: a[k] for k in ("tick", "card", "x", "y", "play_index")} | {"ability": False}
                            for a in actions[0]],
        "ghost_commands": [{k: a[k] for k in ("tick", "card", "x", "y", "play_index")} | {"ability": False}
                           for a in actions[1]],
        "final_decks": final_decks,
    }

    try:
        raw = env.reset(entry)
    except (KeyError, ValueError) as exc:
        raise ReplaySkip(f"simulator reset refused replay: {exc}") from exc
    if getattr(env, "form_fallbacks", None):
        forms = ",".join(f"side{s}:{name}:{form}" for s, name, form in env.form_fallbacks)
        raise ReplaySkip(f"simulator refused deck form: {forms}")
    # Reset infers the correct starting hands from the full action traces. Disable its one-sided
    # ghost scheduler because this recorder drives both seats together below.
    env._ghosts, env._gi, env._pending = [], 0, []
    frames: list[dict] = []
    play_frames: list[dict] = []
    log: list[dict] = []
    by_tick: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for seat in (0, 1):
        for action in actions[seat]:
            by_tick[action["tick"]].append(action)
    event_ticks = sorted(by_tick)
    event_pos = 0
    next_frame = ((int(env.tick) // record_every) + 1) * record_every
    accepted = 0
    while event_pos < len(event_ticks):
        tick = event_ticks[event_pos]
        while next_frame < tick and next_frame <= tail_cap:
            env._advance_to(next_frame)
            if env.terminated:
                break
            frames.append(env.raw())
            next_frame += record_every
        if env.terminated:
            break
        env._advance_to(tick)
        if env.terminated:
            break
        state = env.raw()
        if tick == next_frame:
            frames.append(state)
            next_frame += record_every
        tick_actions = by_tick[tick]
        # Every seat's pre-action example sees the same public state for a simultaneous decision.
        for action in tick_actions:
            play_frames.append({**state, "play_index": action["play_index"]})
        for action in tick_actions:
            result = env.eng.act(side=action["side"], deck_index=action["deck_index"], x=action["x"], y=action["y"])
            if not result.get("accepted"):
                reason = RESULT_CODE_NAMES.get(int(result.get("result_code", -1)), "rejected")
                raise ReplaySkip(f"simulator refused play at {tick}: {reason}")
            accepted += 1
            log.append({"tick": tick, "play_index": action["play_index"], "side": action["side"],
                        "ability": False, "card": action["card"], "x": action["x"], "y": action["y"],
                        "accepted": True, "actual_form": int(env.public_plays[-1]["form"]),
                        "source_tick": action["source_tick"]})
        event_pos += 1

    # Continue the simulated replay to its result for consistent value labels; post-last-play frames
    # are unnecessary because dataset_gen intentionally drops rows where the hand is unknowable.
    if not env.terminated:
        env._advance_to(tail_cap)
    final_state = env.core.state()
    crowns = {str(side): int(final_state.players[side].crowns) for side in (0, 1)}
    winner = int(final_state.winner)
    if winner < 0:
        winner = -1
    rec = {
        "tag": tag, "record_every": record_every, "final_decks": final_decks,
        "frames": frames, "play_frames": play_frames, "log": log,
        "expected": {"crowns_by_side": source_crowns, "result": str(battle.get("result") or "unknown")},
        "final": {"tick": int(final_state.tick), "winner": winner, "crowns": [crowns["0"], crowns["1"]],
                  "terminated": bool(env.terminated)},
        "source": {"dataset": "VanguardX101/IL_Replay", "replay_tag": tag,
                   "state_source": "replayed in RoyaleSim 0.1.17", "abilities_replayed": False},
    }
    stats = {"tag": tag, "source_events": len(events), "source_card_plays": sum(map(len, actions.values())),
             "source_ability_events_skipped": abilities, "simulated_card_plays_accepted": accepted,
             "recorded_frames": len(frames), "play_frames": len(play_frames), "simulated_tick": int(final_state.tick),
             "source_crowns_by_side": source_crowns, "simulated_crowns_by_side": crowns,
             "terminated": bool(env.terminated)}
    if accepted != sum(map(len, actions.values())):
        stats["truncated_before_all_plays"] = True
    return rec, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hf-dir", type=Path, default=DEFAULT_HF, help="Directory containing part-*.parquet")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--clashai-root", type=Path, default=DEFAULT_CLASHAI)
    ap.add_argument("--runtime", default="unpinned-linux", help="ROYALE_RUNTIME profile from ClashAI/pipeline/royale_runtime.py")
    ap.add_argument("--matches", type=int, default=1, help="Successful matches to reconstruct")
    ap.add_argument("--limit-rows", type=int, default=0, help="Maximum source rows to inspect (0 = all)")
    ap.add_argument("--tag", action="append", default=[], help="Only reconstruct these replay tags; may repeat")
    ap.add_argument("--record-every", type=int, default=RECORD_EVERY)
    ap.add_argument("--tail-cap", type=int, default=TAIL_CAP)
    ap.add_argument("--feature-version", type=int, choices=(1, 2, 3), default=3,
                    help="ClashAI dataset feature version (FV3 includes unit forms and opponent play history)")
    a = ap.parse_args(argv)
    if a.matches < 1 or a.record_every < 1 or a.tail_cap < 1:
        ap.error("matches, record-every and tail-cap must be positive")
    hf_dir, out, clashai = a.hf_dir.resolve(), a.out.resolve(), a.clashai_root.resolve()
    if not hf_dir.is_dir():
        ap.error(f"parquet directory does not exist: {hf_dir}")
    if not (clashai / "pipeline" / "dataset_gen.py").is_file():
        ap.error(f"not a ClashAI checkout: {clashai}")
    if out.exists() and any(out.iterdir()):
        ap.error(f"refusing to overwrite non-empty output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    os.environ["ROYALE_RUNTIME"] = a.runtime
    sys.path.insert(0, str(clashai))
    import polars as pl
    from pipeline import dataset_gen
    from pipeline.royale_env import RoyalePoolEnv
    from pipeline.royale_runtime import activate

    runtime_stamp = activate()
    # FV3 adds unit forms and opponent history. The data generator can read the
    # selected runtime's embedded evolution catalog when the source JSON is absent.
    env = RoyalePoolEnv(warmup_ticks=90, seed=0, forms_mode="deck", feature_version=a.feature_version,
                        tail_cap=a.tail_cap)
    card_by_slug: dict[str, str] = {}
    for name in env.names.values():
        slug = dataset_gen.card_key(name)
        if slug:
            card_by_slug.setdefault(base_key(slug), name)
    parts = sorted(hf_dir.glob("part-*.parquet"))
    if not parts:
        ap.error(f"no part-*.parquet files in {hf_dir}")
    wanted = set(a.tag)
    stats: Counter[str] = Counter()
    written: list[dict] = []
    scanned = 0
    started = time.time()
    for part in parts:
        table = pl.read_parquet(part, columns=["replay_tag", "payload_json"])
        part_sha = sha256(part)
        for tag, payload_text in table.iter_rows():
            scanned += 1
            tag = str(tag)
            if wanted and tag not in wanted:
                continue
            if a.limit_rows and scanned > a.limit_rows:
                break
            try:
                rec, result = make_match(json.loads(payload_text), env, card_by_slug,
                                         record_every=a.record_every, tail_cap=a.tail_cap)
            except ReplaySkip as exc:
                stats[str(exc)] += 1
                continue
            target = out / f"replay_{tag}.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(rec, separators=(",", ":")), encoding="utf-8")
            temporary.replace(target)
            written.append({"tag": tag, "path": target.name, "sha256": sha256(target), "source_part": part.name,
                            "source_part_sha256": part_sha, "stats": result})
            print(json.dumps({"written": len(written), **result}, separators=(",", ":")), flush=True)
            if len(written) >= a.matches:
                break
        if len(written) >= a.matches or (a.limit_rows and scanned > a.limit_rows):
            break
    manifest = {"schema": "opencrbot.clashai-sim-corpus.v1", "source": "VanguardX101/IL_Replay",
                "source_dir": str(hf_dir), "runtime": runtime_stamp, "runtime_env": a.runtime,
                "trainer_contract": "ClashAI/pipeline.dataset_gen replay_*.json", "record_every": a.record_every,
                "tail_cap": a.tail_cap, "feature_version": a.feature_version, "source_rows_scanned": scanned,
                "matches_written": len(written),
                "skipped_by_reason": dict(stats), "matches": written,
                "elapsed_seconds": round(time.time() - started, 2)}
    (out / "build_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out), "source_rows_scanned": scanned, "matches_written": len(written),
                      "skipped_by_reason": dict(stats), "elapsed_seconds": manifest["elapsed_seconds"]}, indent=2))
    return 0 if written else 2


if __name__ == "__main__":
    raise SystemExit(main())

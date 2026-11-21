"""Pair the OpenCRBOT baseline or ClashAI policy with a seeded opponent in RoyaleGym."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

from opencrbot.clashai_policy import ClashAIGenFV3Policy, ClashAIS1Policy
from opencrbot.contracts import DetectedUnit, Evidence, PublicObservation
from opencrbot.policy import POLICY_SCHEMA, Recommendation, recommend
from royalegym import ClashParallelEnv, DefaultStateMutator, RustEngine, TileActionParser
import royalegym
from royalegym.protocol import DeployStatus


DECK = ["Knight", "Archer", "Giant", "Minions", "Fireball", "Zap", "Cannon", "Musketeer"]


def card_key(name: str) -> str:
    key = "".join(c for c in name.lower() if c.isalnum())
    return {"archer": "archers", "log": "thelog"}.get(key, key)


def observation(env, state, names, frame_id):
    arena = env.engine.arena()
    unit_types = env.engine.unit_types()
    now = state.tick * state.tick_ms

    def known(value):
        return Evidence(value, "observed", 1.0, "royalegym.public", now)

    unknown = Evidence.unknown("royalegym.unavailable", now)
    units = tuple(DetectedUnit(known(unit_types[e.unit_type]), known("own" if e.team == 0 else "opponent"),
                               known(e.x / arena.width), known(1 - e.y / arena.height), str(e.uid))
                  for e in state.entities if 0 <= e.unit_type < len(unit_types) and e.tower_slot < 0 and e.hp > 0
                  and 0 <= e.x <= arena.width and 0 <= e.y <= arena.height)
    player = state.players[0]
    return PublicObservation(frame_id, "royalegym", now, None, None, known(now / 1000), known(not state.game_over),
                             known(player.elixir_milli / 1000), unknown,
                             tuple(known(names[c]) if c in names else unknown for c in player.hand), units)


def clashai_hint(policy, obs, state, accepted_plays, opponent_plays, ids, deck_slots, engine_names, parser, mask):
    from pipeline import vocab

    hand = [policy.deck.card_ids[deck_slots[card_key(slot.value)]] if slot.value is not None else -1
            for slot in obs.my_hand]
    units = [dict(cls=ids[card_key(vocab.engine_key(u.card.value))], side=0 if u.owner.value == "own" else 1, x=u.x.value, y=u.y.value,
                  hp_frac=None, deploying=None, age_sec=None, conf=1.0, form=0)
             for u in obs.units if vocab.engine_key(u.card.value) and card_key(vocab.engine_key(u.card.value)) in ids]
    towers = []
    for side, player in enumerate(state.players):
        for slot, kind, lane in ((0, "king", None), (1, "princess", "L"), (2, "princess", "R")):
            index = 3 - slot if side == 1 and slot else slot
            hp = player.tower_hp[index]
            towers.append(dict(side=side, kind=kind, lane=lane, alive=hp > 0,
                               hp_frac=hp / player.tower_max_hp[index]))
    my_next = -1
    if isinstance(policy, ClashAIGenFV3Policy):
        next_name = engine_names.get(state.players[0].next_card)
        next_slot = deck_slots.get(card_key(next_name)) if next_name else None
        if next_slot is not None:
            my_next = policy.deck.card_ids[next_slot]
    data = dict(source="engine", t_sec=obs.battle_clock_s.value, t_source="tick", double_elixir=state.elixir_rate >= 2,
                overtime=state.overtime, my_elixir=obs.my_elixir.value, my_elixir_exact=True, opp_elixir=None,
                my_hand=hand, my_next=my_next, towers=towers, units=units, spells=[], deck=policy.deck.card_ids)
    legal_cells = []
    for slot in range(4):
        legal_cells.append([bool(mask[parser.encode(slot, min(parser.nx - 1, int(policy.cell_center(cell, policy.grid)[0] * parser.nx)),
                                                    min(parser.ny - 1, int((1 - policy.cell_center(cell, policy.grid)[1]) * parser.ny)))])
                            for cell in range(36 * 64)])
    kwargs = {"accepted_plays": accepted_plays, "legal_cells": legal_cells}
    if isinstance(policy, ClashAIGenFV3Policy):
        kwargs["opponent_plays"] = opponent_plays
    result = policy.decide(data, **kwargs)
    if not result["play"]:
        return Recommendation(POLICY_SCHEMA, "wait", None, None, None, None, 0.0, result["why"])
    slot = result["hand_slot"]
    return Recommendation(POLICY_SCHEMA, "suggest", slot, obs.my_hand[slot].value,
                          result["board_x"], result["board_y"], 0.0, result["why"])


def battle(seed: int, policy: ClashAIS1Policy | ClashAIGenFV3Policy | None = None,
           deck_override: list[str] | None = None) -> dict:
    engine = RustEngine()
    engine_names = {card.card_id: card.name for card in engine.cards()}
    cards_by_key = {card_key(name): name for name in engine_names.values()}
    deck, forms = list(deck_override or DECK), None
    if policy is not None:
        deck_keys = [card_key(name.removesuffix("_evo").removesuffix("_hero")) for name in policy.deck.cards]
        if len(set(deck_keys)) != 8 or any(key not in cards_by_key for key in deck_keys):
            raise ValueError("Checkpoint deck contains cards that RoyaleGym cannot map")
        deck = [cards_by_key[key] for key in deck_keys]
        forms = [[1 if name.endswith("_evo") else 2 if name.endswith("_hero") else 0 for name in policy.deck.cards], [0] * 8]
        deck_slots = {name: slot for slot, name in enumerate(deck_keys)}
        ids = {card_key(name): card_id for card_id, name in enumerate(policy.vocabulary)}
    elif deck_override is not None:
        deck_keys = [card_key(name) for name in deck_override]
        if len(set(deck_keys)) != 8 or any(key not in cards_by_key for key in deck_keys):
            raise ValueError("Configured deck contains cards that RoyaleGym cannot map")
        deck = [cards_by_key[key] for key in deck_keys]
    opponent_deck = list(DECK)
    env = ClashParallelEnv(engine, state_mutator=DefaultStateMutator(decks=[deck, opponent_deck], forms=forms),
                           action_parser=TileActionParser(), decision_ms=500)
    rng = random.Random(seed)
    events = []
    accepted_plays = []
    opponent_plays = []
    counts = Counter()
    steps = 0
    try:
        env.reset(seed=seed)
        names = {key: "archers" if name.lower() == "archer" else name.lower() for key, name in engine_names.items()}
        parser = env.action_parser
        while env.agents:
            steps += 1
            state = env.engine.state()
            obs = observation(env, state, names, steps)
            rec = recommend(obs) if policy is None else clashai_hint(policy, obs, state, accepted_plays, opponent_plays,
                                                                    ids, deck_slots,
                                                                    engine_names, parser, env.action_masks("blue"))
            action = 0
            event = None
            if rec.status == "suggest":
                x = min(parser.nx - 1, int(rec.target_x * parser.nx))
                y = min(parser.ny - 1, int((1 - rec.target_y) * parser.ny))
                proposed = parser.encode(rec.card_slot, x, y)
                event = {"tick": state.tick, "card": rec.card, "slot": rec.card_slot, "tile": [x, y]}
                if env.action_masks("blue")[proposed]:
                    action = proposed
                else:
                    event["result"] = "masked"
            else:
                counts[f"wait:{rec.reason or 'unknown'}"] += 1
            legal = env.action_masks("red").nonzero()[0][1:] if steps % 4 == 0 else ()
            opponent = int(rng.choice(legal)) if len(legal) else 0
            opponent_event = None
            if isinstance(policy, ClashAIGenFV3Policy) and opponent:
                command = parser.parse(opponent, state, team=1)
                if command is not None and command.hand_slot < 4:
                    card_name = engine_names.get(state.players[1].hand[command.hand_slot])
                    if card_name:
                        arena = env.engine.arena()
                        opponent_event = {
                            "tick": round(obs.battle_clock_s.value / policy.contract.TICK_S),
                            "side": 1,
                            "card": card_name,
                            "form": 0,
                            "x": command.x / arena.width * 18.0,
                            "y": command.y / arena.height * 32.0,
                            "accepted": False,
                        }
            _, _, _, _, info = env.step({"blue": action, "red": opponent})
            if opponent_event is not None:
                opponent_event["accepted"] = info["red"]["deploy_status"] == DeployStatus.OK
                if opponent_event["accepted"]:
                    opponent_plays.append(opponent_event)
            if event is not None:
                if action:
                    status = info["blue"]["deploy_status"]
                    event["result"] = "accepted" if status == DeployStatus.OK else "engine_rejected"
                    event["engine_status"] = int(status)
                    if status == DeployStatus.OK and policy is not None:
                        deck_slot = deck_slots[card_key(rec.card)]
                        accepted_plays.append((state.tick, deck_slot, (x + 0.5) / parser.nx,
                                               1 - (y + 0.5) / parser.ny))
                counts[event["result"]] += 1
                events.append(event)
        state = env.engine.state()
        return {"seed": seed, "deck": deck, "terminal": state.game_over, "ticks": state.tick, "steps": steps,
                "winner": int(state.winner), "crowns": [p.crowns for p in state.players],
                "opponent_history_plays": len(opponent_plays) if isinstance(policy, ClashAIGenFV3Policy) else None,
                "play_counts": dict(counts), "events": events}
    finally:
        env.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    parser.add_argument("--policy", choices=("baseline", "s1", "gen-fv3"), default="baseline")
    parser.add_argument("--clashai-root", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--costs", type=int, nargs=8)
    parser.add_argument("--deck-config", type=Path,
                        help="ClashAI-compatible YAML deck; use the same deck when pairing baseline and model runs")
    parser.add_argument("--compare-baseline", action="store_true",
                        help="run the defensive baseline and selected policy against the same seeded opponent")
    parser.add_argument("--output", type=Path, help="write the complete JSON report to this path")
    args = parser.parse_args()
    options = (args.clashai_root, args.checkpoint, args.costs)
    if args.policy == "baseline" and any(options):
        parser.error("--clashai-root, --checkpoint and --costs are only used with a ClashAI policy")
    if args.policy != "baseline" and not all(options):
        parser.error("ClashAI simulation requires --clashai-root, --checkpoint and --costs")
    if args.policy == "gen-fv3" and args.deck_config is None:
        parser.error("Gen FV3 evaluation requires --deck-config to define the checkpoint's eight-card deck")

    deck_override = None
    if args.deck_config is not None:
        import yaml

        loaded_deck = yaml.safe_load(args.deck_config.read_text(encoding="utf-8"))
        deck_override = loaded_deck.get("cards") if isinstance(loaded_deck, dict) else None
        if not isinstance(deck_override, list) or len(deck_override) != 8:
            parser.error("--deck-config must contain exactly eight card names in a 'cards' list")

    policy = None
    if args.policy == "s1":
        import torch

        torch.set_num_threads(1)
        policy = ClashAIS1Policy(*options)
    elif args.policy == "gen-fv3":
        import torch

        torch.set_num_threads(1)
        policy = ClashAIGenFV3Policy(*options, args.deck_config)
    policy_name = ("ClashAI Gen FV3" if args.policy == "gen-fv3" else
                   "ClashAI S1" if policy else "OpenCRBOT defensive baseline")
    report = {
        "policy": policy_name,
        "checkpoint_sha256": policy.checkpoint_sha256 if policy else None,
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "royalegym_version": royalegym.__version__,
        "opponent": "seeded legal random",
        "opponent_deck": DECK,
        "opponent_history": "accepted seeded opponent plays" if args.policy == "gen-fv3" else None,
        "own_next_card_source": "RoyaleGym engine state.players[0].next_card" if args.policy == "gen-fv3" else None,
        "deck_config": str(args.deck_config.resolve()) if args.deck_config else None,
        "seeds": args.seeds,
    }
    if args.compare_baseline:
        if policy is None:
            parser.error("--compare-baseline requires --policy s1 or --policy gen-fv3")
        paired = []
        outcomes = {"baseline": Counter(), "policy": Counter()}
        for seed in args.seeds:
            baseline_result = battle(seed, None, deck_override)
            policy_result = battle(seed, policy, deck_override)
            paired.append({"seed": seed, "baseline": baseline_result, "policy": policy_result})
            for label, result in (("baseline", baseline_result), ("policy", policy_result)):
                outcome = ("truncated" if not result["terminal"] else
                           "win" if result["winner"] == 0 else "loss" if result["winner"] == 1 else "draw")
                outcomes[label][outcome] += 1
        report["paired_matches"] = paired
        report["summary"] = {label: dict(counts) for label, counts in outcomes.items()}
    else:
        report["matches"] = [battle(seed, policy, deck_override) for seed in args.seeds]

    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)

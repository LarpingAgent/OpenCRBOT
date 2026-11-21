from __future__ import annotations

from hashlib import file_digest
from pathlib import Path
import sys

from .clashai_export import to_board_state
from .contracts import PublicObservation
from .policy import POLICY_SCHEMA, Recommendation


def _verified_play_entry(policy, event: dict) -> tuple[int, int, float, float]:
    verification = event.get("verification") or {}
    if event.get("status") != "consistent_with_play" or verification.get("status") != "consistent_with_play":
        raise ValueError("Only a verified play can enter policy history")
    observation = event.get("before_observation")
    proposal = event.get("action_proposal")
    if not isinstance(observation, dict) or not isinstance(proposal, dict):
        raise ValueError("Verified action is missing its observation or proposal")
    board = to_board_state(observation, policy.vocabulary, list(policy.deck.cards))
    hand_slot = proposal.get("card_slot")
    if type(hand_slot) is not int or not 0 <= hand_slot < len(board["my_hand"]):
        raise ValueError("Verified action has an invalid hand slot")
    selected_name = observation["my_hand"][hand_slot].get("value")
    if selected_name != proposal.get("expected_card"):
        raise ValueError("Verified action card does not match its source hand slot")
    card_id = board["my_hand"][hand_slot]
    if card_id not in board["deck"]:
        raise ValueError("Verified card is outside the configured deck")
    x, y = proposal.get("target_x"), proposal.get("target_y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        raise ValueError("Verified action is missing its board target")
    tick = round(board["t_sec"] / policy.contract.TICK_S)
    return tick, board["deck"].index(card_id), float(x), float(y)


class ClashAIS1Policy:
    """Keep one upstream S1 model loaded for offline and live recommendations."""

    def __init__(self, root: Path, checkpoint: Path, costs: list[int]) -> None:
        if not checkpoint.is_file():
            raise ValueError(f"Checkpoint not found: {checkpoint}")
        if len(costs) != 8 or any(not 0 <= cost <= 10 for cost in costs):
            raise ValueError("Expected eight card costs between 0 and 10")
        if not (root / "pipeline" / "obs_contract.py").is_file():
            raise ValueError(f"ClashAI checkout not found: {root}")
        sys.path.insert(0, str(root.resolve()))
        try:
            from pipeline import dataset, e1_eval, obs_contract, policy_io, vocab
        except ImportError as exc:
            raise RuntimeError(f"Cannot import ClashAI dependencies: {exc}") from exc
        finally:
            sys.path.pop(0)

        self.model, info = policy_io.load_model(checkpoint, "cpu", retries=1)
        if not info.get("deck"):
            raise ValueError("Checkpoint must identify its S1 deck")
        self.deck = obs_contract.load_deck(info["deck"])
        self.grid = info["grid"]
        self.costs = costs
        self.policy_name = "clashai-s1"
        self.contract = obs_contract
        self.eval = e1_eval
        self.vocabulary = list(vocab.UNIT_VOCAB)
        self.cell_center = policy_io.cell_center
        self.encode_history = dataset._past
        self.accepted_plays = []
        with checkpoint.open("rb") as stream:
            self.checkpoint_sha256 = file_digest(stream, "sha256").hexdigest()

    def decide(self, data: dict, *, accepted_plays=(), legal_cells=None) -> dict:
        if tuple(data["deck"]) != self.deck.card_ids:
            raise ValueError("Board deck and checkpoint deck must match in deck-slot order")
        board = self.contract.BoardState(**{
            **data,
            "towers": tuple(self.contract.Tower(**tower) for tower in data["towers"]),
            "units": tuple(self.contract.Unit(**unit) for unit in data["units"]),
            "spells": tuple(self.contract.Unit(**unit) for unit in data["spells"]),
        })
        tokens, mask, scalars = self.contract.to_tokens(board)
        history = self.encode_history(accepted_plays, round(board.t_sec / self.contract.TICK_S))
        encoded, heads, gate, hand = self.eval.model_forward(self.model, tokens, mask, scalars, history)
        allowed = self.eval.allowed_slots(hand, self.costs, int(board.my_elixir))
        decision = self.eval.live_decide(self.model, encoded, heads, gate, allowed,
                                         tau=self.eval.TAU_LIVE, stalled=False)
        if decision["play"]:
            card_id = board.deck[decision["slot"]]
            decision["hand_slot"] = board.my_hand.index(card_id)
            if legal_cells is not None:
                import torch

                legal = torch.as_tensor(legal_cells[decision["hand_slot"]], dtype=torch.bool)
                if legal.numel() != 36 * 64:
                    raise ValueError("Expected a 36 by 64 placement mask")
                if not legal.any():
                    decision.update(play=False, cell=-1, why="no_legal_cell")
                else:
                    with torch.no_grad():
                        logits = self.model.cell_logits(encoded, torch.tensor([decision["slot"]]))[0]
                        decision["cell"] = int(logits.masked_fill(~legal, float("-inf")).argmax())
        if decision["play"]:
            decision["board_x"], decision["board_y"] = self.cell_center(decision["cell"], self.grid)
        decision.update(checkpoint_sha256=self.checkpoint_sha256, gate_probability=gate)
        return decision

    def record_verified_action(self, event: dict) -> dict:
        entry = _verified_play_entry(self, event)
        self.accepted_plays.append(entry)
        return {"status": "recorded", "accepted_play_count": len(self.accepted_plays), "tick": entry[0],
                "deck_slot": entry[1], "target": [entry[2], entry[3]]}

    def recommend(self, observation: PublicObservation) -> Recommendation:
        try:
            board = to_board_state(observation.to_dict(), self.vocabulary, list(self.deck.cards))
        except ValueError as exc:
            return Recommendation(POLICY_SCHEMA, "wait", None, None, None, None, 0.0, f"ClashAI skipped: {exc}")
        decision = self.decide(board, accepted_plays=self.accepted_plays)
        if not decision["play"]:
            return Recommendation(POLICY_SCHEMA, "wait", None, None, None, None, 0.0,
                                  f"ClashAI: {decision['why']}")
        slot = decision["hand_slot"]
        return Recommendation(POLICY_SCHEMA, "suggest", slot, observation.my_hand[slot].value,
                              decision["board_x"], decision["board_y"], 0.0, "ClashAI S1 suggested placement")


class ClashAIGenFV3Policy:
    """Use a ClashAI GenModel FV3 checkpoint through ClashAI's own S1-compatible inference adapter."""

    def __init__(self, root: Path, checkpoint: Path, costs: list[int], deck: str | Path) -> None:
        if not checkpoint.is_file():
            raise ValueError(f"Checkpoint not found: {checkpoint}")
        if len(costs) != 8 or any(not 0 <= cost <= 10 for cost in costs):
            raise ValueError("Expected eight card costs between 0 and 10")
        if not (root / "pipeline" / "model_gen.py").is_file():
            raise ValueError(f"ClashAI checkout not found: {root}")

        import torch

        sys.path.insert(0, str(root.resolve()))
        try:
            from pipeline import dataset, dataset_gen, e1_eval, model_gen, obs_contract, policy_io, vocab
        except ImportError as exc:
            raise RuntimeError(f"Cannot import ClashAI GenModel dependencies: {exc}") from exc
        finally:
            sys.path.pop(0)

        self.model, info = model_gen.load_model(checkpoint, torch.device("cpu"))
        self.model.eval()
        if not info.get("gen"):
            raise ValueError("Checkpoint is not a ClashAI generalist model")
        feature_version = int(info.get("args", {}).get("feature_version", 1))
        if feature_version != 3:
            raise ValueError(f"This adapter supports GenModel FV3 checkpoints, got FV{feature_version}")
        if getattr(self.model, "mem", None) is not None or self.model.opp.get("timing") or self.model.opp.get("ctx_tokens"):
            raise ValueError("This adapter supports the plain FV3 GenModel input contract only")

        self.deck = obs_contract.load_deck(deck)
        self.costs = costs
        self.policy_name = "clashai-gen-fv3"
        self.contract = obs_contract
        self.vocabulary = list(vocab.UNIT_VOCAB)
        self.eval = e1_eval
        self.dataset = dataset
        self.dataset_gen = dataset_gen
        self.gen_policy = e1_eval.GenPolicy(self.model, info["card_vocab"], info.get("opp_sim"))
        engine_deck = [self._engine_card_name(name) for name in self.deck.cards]
        missing = sorted({dataset_gen.card_key(name) for name in engine_deck} - set(self.gen_policy.gid))
        if missing:
            raise ValueError(f"Checkpoint card vocabulary does not cover configured deck: {', '.join(missing)}")
        self.slot_card, self.slot_form = self.gen_policy.slot_ident(engine_deck, dict(enumerate(range(8))))
        if len(set(self.slot_card[:8].tolist())) != 8:
            raise ValueError("GenModel deck must contain eight distinct base card identities")
        self.grid = str(info["args"].get("grid", "lattice"))
        self.cell_center = policy_io.cell_center
        self.accepted_plays = []
        with checkpoint.open("rb") as stream:
            self.checkpoint_sha256 = file_digest(stream, "sha256").hexdigest()

    @staticmethod
    def _engine_card_name(name: str) -> str:
        if name.endswith("_evo"):
            return name[:-4] + "@evolution"
        if name.endswith("_hero"):
            return name[:-5] + "@hero"
        return name

    def _board(self, data: dict):
        return self.contract.BoardState(**{
            **data,
            "towers": tuple(self.contract.Tower(**tower) for tower in data["towers"]),
            "units": tuple(self.contract.Unit(**unit) for unit in data["units"]),
            "spells": tuple(self.contract.Unit(**unit) for unit in data["spells"]),
        })

    def decide(self, data: dict, *, accepted_plays=(), opponent_plays=(), legal_cells=None) -> dict:
        import numpy as np
        import torch

        board = self._board(data)
        if tuple(board.deck) != self.deck.card_ids:
            raise ValueError("Board deck and configured deck must match in deck-slot order")
        tick = round(board.t_sec / self.contract.TICK_S)
        tok, mask, sc = self.contract.to_tokens(board)
        past = self.dataset._past(accepted_plays, tick)
        row = self.gen_policy.row(tok, mask, sc, past, self.slot_card, self.slot_form)
        row["unit_form"] = self.contract.to_unit_forms(board)
        row["opp_past"] = self.dataset_gen.opponent_past(opponent_plays, tick, 0, self.gen_policy.gid)

        enc, heads, probabilities, hand = self.gen_policy.forward_batch([row], device="cpu")
        allowed = self.eval.allowed_slots(hand[0], self.costs, int(board.my_elixir))
        decision = self.eval.live_decide(self.gen_policy, enc, heads, probabilities[0], allowed,
                                         tau=self.eval.TAU_LIVE, stalled=False, device="cpu")
        decision["checkpoint_sha256"] = self.checkpoint_sha256
        decision["gate_probability"] = probabilities[0]
        decision["opponent_history_rows"] = int(np.count_nonzero(row["opp_past"][:, 0]))
        if decision["play"]:
            slot = decision["slot"]
            card_id = board.deck[slot]
            decision["hand_slot"] = board.my_hand.index(card_id)
            if legal_cells is not None:
                legal = torch.as_tensor(legal_cells[decision["hand_slot"]], dtype=torch.bool)
                if legal.numel() != 36 * 64:
                    raise ValueError("Expected a 36 by 64 placement mask")
                if not legal.any():
                    decision.update(play=False, cell=-1, why="no_legal_cell")
                else:
                    with torch.no_grad():
                        logits = self.gen_policy.cell_logits(enc, torch.tensor([slot]))[0]
                        decision["cell"] = int(logits.masked_fill(~legal, float("-inf")).argmax())
            if decision["play"]:
                decision["board_x"], decision["board_y"] = self.cell_center(decision["cell"], self.grid)
        return decision

    def record_verified_action(self, event: dict) -> dict:
        entry = _verified_play_entry(self, event)
        self.accepted_plays.append(entry)
        return {"status": "recorded", "accepted_play_count": len(self.accepted_plays), "tick": entry[0],
                "deck_slot": entry[1], "target": [entry[2], entry[3]]}

    def recommend(self, observation: PublicObservation) -> Recommendation:
        try:
            board = to_board_state(observation.to_dict(), self.vocabulary, list(self.deck.cards))
        except ValueError as exc:
            return Recommendation(POLICY_SCHEMA, "wait", None, None, None, None, 0.0,
                                  f"ClashAI skipped: {exc}")
        decision = self.decide(board, accepted_plays=self.accepted_plays)
        history_note = (f"{len(self.accepted_plays)} verified own plays; "
                        "opponent-play history is unavailable from screenshots")
        if not decision["play"]:
            return Recommendation(POLICY_SCHEMA, "wait", None, None, None, None, 0.0,
                                  f"ClashAI Gen FV3: {decision['why']}; {history_note}.")
        slot = decision["hand_slot"]
        return Recommendation(POLICY_SCHEMA, "suggest", slot, observation.my_hand[slot].value,
                              decision["board_x"], decision["board_y"], 0.0,
                              f"ClashAI Gen FV3 suggestion; {history_note}.")


def load_clashai_policy(root: Path, checkpoint: Path, costs: list[int], deck: str | Path | None = None):
    """Load the S1 or explicitly configured Gen FV3 adapter for a ClashAI checkpoint."""
    import torch

    try:
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    except TypeError:  # PyTorch versions before the weights_only option
        state = torch.load(checkpoint, map_location="cpu")
    if isinstance(state, dict) and state.get("gen"):
        if deck is None:
            raise ValueError("A generalist checkpoint requires --deck to identify its eight-card deck.")
        return ClashAIGenFV3Policy(root, checkpoint, costs, deck)
    return ClashAIS1Policy(root, checkpoint, costs)

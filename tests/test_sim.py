import pytest

from holdem import sim
from holdem.core.betting import Action, LegalActions

FOLD, CHECK, CALL, BET, RAISE, ALL_IN = Action

FACING_BET = LegalActions(frozenset({FOLD, CALL, RAISE, ALL_IN}), call_amount=2, min_raise_to=4, max_raise_to=100)
UNOPENED = LegalActions(frozenset({FOLD, CHECK, BET, ALL_IN}), min_raise_to=2, max_raise_to=100)


@pytest.mark.parametrize(
    "text, legal, expected",
    [
        ("f", FACING_BET, (FOLD, None)),
        ("fold", FACING_BET, (FOLD, None)),
        ("c", FACING_BET, (CALL, None)),
        (" Call ", FACING_BET, (CALL, None)),
        ("r 10", FACING_BET, (RAISE, 10)),
        ("raise 100", FACING_BET, (RAISE, 100)),
        ("a", FACING_BET, (ALL_IN, None)),
        ("allin", FACING_BET, (ALL_IN, None)),
        ("x", UNOPENED, (CHECK, None)),
        ("k", UNOPENED, (CHECK, None)),
        ("b 6", UNOPENED, (BET, 6)),
        ("bet 2", UNOPENED, (BET, 2)),
    ],
)
def test_parse_action_accepts_short_and_long_forms(text, legal, expected):
    assert sim.parse_action(text, legal) == expected


@pytest.mark.parametrize(
    "text, legal",
    [
        ("", FACING_BET),
        ("dance", FACING_BET),
        ("x", FACING_BET),
        ("r", FACING_BET),
        ("r ten", FACING_BET),
        ("r 3", FACING_BET),
        ("r 101", FACING_BET),
        ("b 10", FACING_BET),
        ("c", UNOPENED),
    ],
)
def test_parse_action_rejects_bad_input_with_a_message(text, legal):
    with pytest.raises(ValueError) as error:
        sim.parse_action(text, legal)
    assert str(error.value)


def test_describe_options_lists_amounts():
    assert sim.describe_options(FACING_BET) == "fold (f) / call 2 (c) / raise to 4-100 (r N) / all-in (a)"
    assert sim.describe_options(UNOPENED) == "fold (f) / check (x) / bet 2-100 (b N) / all-in (a)"


NAMES = {0: "P1", 1: "P2", 2: "P3"}


@pytest.mark.parametrize(
    "event, expected",
    [
        ({"type": "blind", "seat": 1, "blind": "small", "amount": 1, "all_in": False}, "P2 posts small blind 1"),
        ({"type": "blind", "seat": 2, "blind": "big", "amount": 1, "all_in": True}, "P3 posts big blind 1 (all-in)"),
        ({"type": "hole_cards", "seat": 0, "cards": ["AS", "TD"]}, "P1 is dealt [A♠] [10♦]"),
        ({"type": "hole_cards", "seat": 1}, "P2 is dealt two cards"),
        ({"type": "burn"}, None),
        ({"type": "action", "seat": 0, "action": "fold", "amount": 0, "to": 0, "all_in": False}, "P1 folds"),
        ({"type": "action", "seat": 0, "action": "check", "amount": 0, "to": 2, "all_in": False}, "P1 checks"),
        ({"type": "action", "seat": 0, "action": "call", "amount": 2, "to": 2, "all_in": False}, "P1 calls 2"),
        ({"type": "action", "seat": 0, "action": "bet", "amount": 6, "to": 6, "all_in": False}, "P1 bets 6"),
        ({"type": "action", "seat": 1, "action": "raise", "amount": 12, "to": 13, "all_in": True}, "P2 raises to 13 (all-in)"),
        ({"type": "street", "street": "flop", "cards": ["AH", "9C", "5S"]}, "--- Flop: [A♥] [9♣] [5♠]"),
        ({"type": "street", "street": "river", "cards": ["2C"]}, "--- River: [2♣]"),
        ({"type": "uncalled_returned", "seat": 1, "amount": 10}, "Uncalled 10 returned to P2"),
        (
            {"type": "showdown", "seat": 0, "cards": ["AS", "AD"], "best": ["AS", "AD", "AH", "JH", "9C"], "hand": "hand_three_of_a_kind"},
            "P1 shows [A♠] [A♦]: Three of a Kind ([A♠] [A♦] [A♥] [J♥] [9♣])",
        ),
        ({"type": "award", "seat": 0, "amount": 14, "pot": 0}, "P1 wins 14 from the main pot"),
        ({"type": "award", "seat": 2, "amount": 100, "pot": 1}, "P3 wins 100 from side pot 1"),
        ({"type": "hand_end"}, None),
    ],
)
def test_describe_event(event, expected):
    assert sim.describe_event(event, NAMES) == expected


class Script:
    """Feeds answers to the simulator's prompts and records everything it prints."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.lines = []

    def input(self, prompt):
        self.lines.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def print(self, *parts):
        self.lines.append(" ".join(str(part) for part in parts))

    @property
    def text(self):
        return "\n".join(self.lines)


def test_heads_up_hand_played_from_prompts():
    script = Script("c", "x", "b 10", "f", "q")

    code = sim.run(["--players", "2", "--seed", "1"], input_fn=script.input, out=script.print)

    assert code == 0
    assert "P1 calls 1" in script.text
    assert "P2 bets 10" in script.text
    assert "Uncalled 10 returned to P2" in script.text
    assert "P2 wins 4 from the main pot" in script.text


def test_bad_input_is_explained_and_asked_again():
    script = Script("raise 1", "dance", "f", "q")

    sim.run(["--players", "2", "--seed", "1"], input_fn=script.input, out=script.print)

    assert "raise must go to between 4 and 200" in script.text
    assert "Unknown action" in script.text
    assert "P1 folds" in script.text


def test_table_view_marks_positions_and_the_player_to_act():
    script = Script("q")

    sim.run(["--players", "3", "--seed", "1"], input_fn=script.input, out=script.print)

    view = script.text
    assert "Preflop" in view
    assert "> D  P1" in view
    assert "  SB P2" in view
    assert "  BB P3" in view


def test_all_bot_game_runs_without_prompts_and_keeps_chips():
    script = Script()

    code = sim.run(
        ["--players", "6", "--bots", "1,2,3,4,5,6", "--hands", "30", "--seed", "3", "--stack", "50"],
        input_fn=script.input,
        out=script.print,
    )

    assert code == 0
    assert not script.answers
    assert "Hand 1 " in script.text
    final = [line for line in script.lines if line.startswith("Final stacks:")]
    assert len(final) == 1
    assert sum(int(part.split("=")[1]) for part in final[0].split(":")[1].split(",")) == 300


@pytest.mark.parametrize(
    "argv",
    [["--players", "1"], ["--players", "10"], ["--bots", "4"], ["--bots", "x"], ["--blinds", "2/1"], ["--blinds", "1-2"]],
)
def test_bad_arguments_exit_with_an_error(argv, capsys):
    with pytest.raises(SystemExit) as exit_info:
        sim.run(argv, input_fn=Script().input, out=Script().print)
    assert exit_info.value.code == 2

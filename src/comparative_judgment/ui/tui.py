"""The terminal comparison loop.

This module holds **no session state**. Not the pending pair, not an undo stack,
not a cursor. Every keypress calls the session API and then re-reads whatever it
needs to draw. That is the discipline D3 exists to enforce: a widget that kept
its own pending pair would work perfectly and would have to be thrown away when
the browser adapter arrives, because the state it accumulated would live in the
wrong place.

The one piece of UI judgment that is not arbitrary: **undo is a first-class key,
not a buried command.** In a session of several hundred comparisons a misfired
keypress is certain rather than possible, and without an easy retraction it
becomes a silently wrong datum in the log that everything downstream derives
from. It costs one binding.
"""

from __future__ import annotations

from typing import ClassVar, Final

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static

from comparative_judgment.core.errors import NothingToJudgeError
from comparative_judgment.core.models import Finding, Outcome, PairForReview
from comparative_judgment.core.session import Session

_CSS: Final[str] = """
Screen { layout: vertical; }
#progress { height: 3; padding: 1 2; background: $panel; }
#panes { height: 1fr; }
.card { width: 1fr; padding: 1 2; border: round $primary; }
.card-title { text-style: bold; }
.label { color: $text-muted; text-style: italic; }
#done { padding: 2 4; }
"""


def _render_card(finding: Finding, side: str, appearances: int, target: int) -> str:
    """Turn a finding into the text of one pane.

    Formatting lives here, in the front end, and never in the core. The core
    hands over `observation`, `evidence` and `consequence` as fields; a browser
    adapter will lay the same fields out as HTML without this module being
    involved.
    """
    fragments = "\n".join(f"    • {fragment}" for fragment in finding.evidence)
    return (
        f"[{side}]  {finding.id}   ({appearances}/{target} appearances)\n\n"
        f"{finding.observation}\n\n"
        f"evidence:\n{fragments}\n\n"
        f"consequence:\n    {finding.consequence}"
    )


class ComparisonApp(App[int]):
    """Two findings, one question: which of these is worse?"""

    CSS = _CSS
    # ClassVar because textual reads these off the class, and ruff is right that
    # a bare mutable class attribute is a trap even when the framework owns it.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left,a", "choose_left", "◀ left is worse"),
        Binding("right,d", "choose_right", "right is worse ▶"),
        Binding("t", "tie", "too close to call"),
        Binding("u", "undo", "undo"),
        Binding("q", "quit_app", "save & quit"),
    ]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield Static("", id="progress")
        with Horizontal(id="panes"):
            with Vertical(classes="card"):
                yield Static("", id="left")
            with Vertical(classes="card"):
                yield Static("", id="right")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    # -- drawing -----------------------------------------------------------

    def _refresh(self) -> None:
        """Re-read everything from the session. Nothing is cached here."""
        pair = self._session.next_pair()
        progress = self._session.progress()

        if pair is None:
            # There is no pair for three different reasons, and only one of them
            # is good news. Saying "complete" for all three is how a rater ends a
            # sitting believing a broken cut was a finished batch.
            if progress.blocked_reason:
                self.query_one("#progress", Static).update(
                    f"BLOCKED — {progress.comparisons_spent} comparisons recorded, "
                    "and nothing further can be judged"
                )
                self.query_one("#left", Static).update(
                    f"{progress.blocked_reason}\n\n"
                    "Nothing is lost: every judgment is in the log. Fix the cuts with "
                    "`cj cuts` and this picks up where it stopped."
                )
            elif progress.placing:
                self.query_one("#progress", Static).update(
                    f"placement complete — {progress.comparisons_spent} comparisons; "
                    "every item has been placed against the cuts"
                )
                self.query_one("#left", Static).update(
                    "Nothing left to place.\n\nNext: `cj bands`, then `cj export`."
                )
            else:
                self.query_one("#progress", Static).update(
                    f"batch complete — {progress.comparisons_spent} comparisons, "
                    f"every item at {progress.appearance_target} appearances"
                )
                self.query_one("#left", Static).update(
                    "Nothing left to judge.\n\nNext: set the three band cuts with `cj cuts`."
                )
            self.query_one("#right", Static).update("")
            return

        self._update_progress(pair, progress.mean_appearances, progress.items_below_target)
        self.query_one("#left", Static).update(
            _render_card(pair.left, "A", pair.appearances_left, pair.appearance_target)
        )
        self.query_one("#right", Static).update(
            _render_card(pair.right, "D", pair.appearances_right, pair.appearance_target)
        )

    def _update_progress(
        self, pair: PairForReview, mean_appearances: float, remaining: tuple[str, ...]
    ) -> None:
        self.query_one("#progress", Static).update(
            f"Which is WORSE?    "
            f"comparisons: {pair.comparisons_spent}    "
            f"mean appearances: {mean_appearances:.1f}/{pair.appearance_target}    "
            f"items still short: {len(remaining)}"
        )

    # -- actions -----------------------------------------------------------

    def _record(self, outcome: Outcome) -> None:
        """Pass the keypress through; a press after the end is inert.

        The session is asked once, not twice: checking `next_pair()` here and
        letting `record()` derive it again doubled the per-keypress work, and the
        refusal is already a named one to catch.
        """
        try:
            self._session.record(outcome)
        except NothingToJudgeError:
            return
        self._refresh()

    def action_choose_left(self) -> None:
        self._record(Outcome.LEFT)

    def action_choose_right(self) -> None:
        self._record(Outcome.RIGHT)

    def action_tie(self) -> None:
        """A tie is an answer, not a refusal to answer.

        Forcing a winner on a genuinely equal pair would put a coin-flip into the
        log indistinguishable from a real judgment.
        """
        self._record(Outcome.TIE)

    def action_undo(self) -> None:
        self._session.undo()
        self._refresh()

    def action_quit_app(self) -> None:
        """Every judgment is already on disk; there is nothing to save."""
        self.exit(0)


def run_comparison_app(session: Session) -> int:
    """Run the terminal loop, returning a process exit code."""
    result = ComparisonApp(session).run()
    return 0 if result is None else result

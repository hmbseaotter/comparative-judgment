"""Command line entry points.

Every subcommand drives the session API and nothing reaches around it. That is
the same discipline the terminal UI follows, and having two front ends over one
interface is what keeps the interface honest — a seam with a single consumer
drifts toward that consumer without anyone noticing.

There is no implicit store path anywhere here (D9). ``--store`` is required on
every command that touches one, because a tool that writes findings text
somewhere by default will eventually do so on a machine where those findings are
real and the person did not choose the location.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from comparative_judgment.core import bands as bands_module
from comparative_judgment.core.errors import ComparativeJudgmentError
from comparative_judgment.core.findings import load_findings
from comparative_judgment.core.graph import components
from comparative_judgment.core.models import CUT_ORDER, Cut
from comparative_judgment.core.pairing import APPEARANCE_TARGET
from comparative_judgment.core.session import Session
from comparative_judgment.core.severity import write_severity_file
from comparative_judgment.core.store import Store

#: The location this project's own documentation and examples use. It is
#: gitignored so that following the examples cannot produce a file that quietly
#: wants committing — but it is a convention, never a fallback: omitting
#: --store is an error, not a prompt to use this.
CONVENTIONAL_STORE = ".cj-store"


def _session(args: argparse.Namespace, *, target: int = APPEARANCE_TARGET) -> Session:
    store = Store.open(Path(args.store))
    return Session(store, rater_id=args.rater, appearance_target=target)


def cmd_init(args: argparse.Namespace) -> int:
    store = Store.create(Path(args.store))
    print(f"created store at {store.path}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    store = Store.open(Path(args.store))
    result = load_findings(Path(args.findings))

    # A finding whose text changed since it was judged is refused by default.
    # Carrying the old judgments over is a decision a human makes, not one the
    # tool makes quietly -- and accepting it is written into the append-only log
    # so the acceptance is auditable rather than invisible.
    revisions = store.pending_revisions(result.admitted)
    if revisions and not args.accept_revisions:
        print("REFUSED: text has changed on finding(s) that were already judged.", file=sys.stderr)
        for revision in revisions:
            print(
                f"  {revision.finding_id}: {revision.old_hash[:12]} -> {revision.new_hash[:12]}"
                f"  ({revision.comparisons} comparison(s) made against the old text)",
                file=sys.stderr,
            )
        print(file=sys.stderr)
        print(
            "Those judgments were made against wording nobody has compared since.", file=sys.stderr
        )
        print(
            "Re-run with --accept-revisions to carry them over; the acceptance is", file=sys.stderr
        )
        print(
            "recorded in the log with your rater id and the hashes it moved between.",
            file=sys.stderr,
        )
        return 1

    for revision in revisions:
        store.append_revision(revision, rater_id=args.rater, session_id="load")
        print(
            f"accepted revision: {revision.finding_id} "
            f"({revision.comparisons} prior comparison(s) carried over)"
        )

    store.put_findings(result.admitted)
    store.put_load_summary(result.excluded_questions)

    print(f"admitted {len(result.admitted)} finding(s)")
    if result.excluded_questions:
        # Reported, never silent: a rater who expected seventy and sees
        # sixty-three needs to know why, and "they were questions" is a very
        # different situation from "they were malformed".
        listed = ", ".join(result.excluded_questions)
        print(f"excluded {result.excluded_count} question-tier entr(ies): {listed}")
        print("  question rows are non-defects; rating one would put an item with no")
        print("  consequence into the anchor set, distorting every later placement.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session = _session(args, target=args.target)
    progress = session.progress()
    print(f"admitted            {progress.admitted}")
    print(f"excluded questions  {progress.excluded_questions}")
    print(f"comparisons spent   {progress.comparisons_spent}  (ties: {progress.ties})")
    print(f"appearance target   {progress.appearance_target}")
    print(
        f"appearances         min {progress.min_appearances}, mean {progress.mean_appearances:.2f}"
    )
    print(f"comparisons/item    {session.mean_comparisons_per_item():.2f}")
    print(f"complete            {'yes' if progress.complete else 'no'}")
    if progress.items_below_target:
        shown = ", ".join(progress.items_below_target[:8])
        more = (
            ""
            if len(progress.items_below_target) <= 8
            else f" (+{len(progress.items_below_target) - 8} more)"
        )
        print(f"below target        {shown}{more}")

    parts = components(
        [e.finding_id for e in session.estimates()], session._store.active_comparisons()
    )
    if len(parts) > 1:
        print(f"\nWARNING: {len(parts)} disconnected component(s).")
        print("  Scale values are not comparable across them: two groups never judged")
        print("  against each other have their relative position set by the prior,")
        print("  not by anything you decided.")
        for group in parts:
            print(f"    - {len(group)} item(s): {', '.join(group[:6])}")
    return 0


def cmd_fit(args: argparse.Namespace) -> int:
    session = _session(args, target=args.target)
    result = session._fit()
    print(f"converged in {result.iterations} iteration(s); {result.ties} tie(s) excluded")
    print(f"regularisation lambda = {result.regularisation}")
    print()
    print(f"{'finding':<16} {'theta':>9} {'app':>5} {'W':>4} {'L':>4} {'T':>4}")
    for estimate in result.ranked():
        print(
            f"{estimate.finding_id:<16} {estimate.theta:>9.4f} {estimate.appearances:>5} "
            f"{estimate.wins:>4} {estimate.losses:>4} {estimate.ties:>4}"
        )
    return 0


def cmd_cuts(args: argparse.Namespace) -> int:
    """Set the three band cuts, as anchor pairs.

    The only absolute judgments the tool ever asks for, and there are exactly
    three of them regardless of how many findings there are.
    """
    session = _session(args, target=args.target)
    ranked = session._fit().ranked()
    if len(ranked) < 4:
        print("need at least four findings to place three cuts", file=sys.stderr)
        return 1

    pairs = [args.critical_high, args.high_medium, args.medium_low]
    cuts: list[Cut] = []
    for name, spec in zip(CUT_ORDER, pairs, strict=True):
        above, _, below = spec.partition(":")
        if not above or not below:
            print(f"--{name.value.replace('_', '-')} must read ABOVE:BELOW", file=sys.stderr)
            return 1
        cuts.append(Cut(name=name, above_id=above, below_id=below))

    session._store.put_cuts(cuts)
    theta = {e.finding_id: e.theta for e in ranked}
    values = bands_module.thresholds(tuple(cuts), theta)
    print("cuts stored as anchor pairs (not as numbers -- a pairwise scale has no")
    print("absolute origin, so a stored threshold means something only relative to")
    print("the fit that produced it):")
    for cut, value in zip(cuts, values, strict=True):
        print(
            f"  {cut.name.value:<14} {cut.above_id} | {cut.below_id}   threshold now {value:+.4f}"
        )
    return 0


def cmd_bands(args: argparse.Namespace) -> int:
    session = _session(args, target=args.target)
    placement = session.place()
    for assignment in sorted(placement.assignments, key=lambda a: (-a.theta, a.finding_id)):
        print(f"{assignment.finding_id:<16} {assignment.band.value:<9} {assignment.theta:+.4f}")
    if placement.unplaced:
        print()
        print(f"unplaced ({len(placement.unplaced)}): {', '.join(placement.unplaced)}")
        print("  no comparison behind these, so banding them would report the prior")
        print("  rather than a judgment.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    session = _session(args, target=args.target)
    placement = session.place()
    path = Path(args.out)
    write_severity_file(
        path,
        assignments=placement.assignments,
        store=session._store,
        unplaced=placement.unplaced,
    )
    print(f"wrote {len(placement.assignments)} severity assignment(s) to {path}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from comparative_judgment.ui.tui import run_comparison_app

    session = _session(args, target=args.target)
    return run_comparison_app(session)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cj",
        description=(
            "Assign severity by pairwise comparison. The store path is always "
            f"explicit; these docs use {CONVENTIONAL_STORE}/, which is gitignored."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_store(
        sub: argparse.ArgumentParser, *, rater: bool = False, target: bool = False
    ) -> None:
        sub.add_argument("--store", required=True, help="path to the store directory")
        if rater:
            sub.add_argument("--rater", default="unnamed", help="who is judging")
        if target:
            sub.add_argument(
                "--target",
                type=int,
                default=APPEARANCE_TARGET,
                help="appearances per item before the batch reports complete",
            )

    init = subparsers.add_parser("init", help="create a new store")
    init.add_argument("--store", required=True)
    init.set_defaults(func=cmd_init)

    load = subparsers.add_parser("load", help="read a YAML findings document")
    load.add_argument("--store", required=True)
    load.add_argument("--findings", required=True)
    load.add_argument("--rater", default="unnamed", help="who is accepting any revisions")
    load.add_argument(
        "--accept-revisions",
        action="store_true",
        help="carry judgments over onto changed text (recorded in the log)",
    )
    load.set_defaults(func=cmd_load)

    compare = subparsers.add_parser("compare", help="judge pairs in the terminal")
    add_store(compare, rater=True, target=True)
    compare.set_defaults(func=cmd_compare)

    status = subparsers.add_parser("status", help="progress and measured cost figures")
    add_store(status, rater=True, target=True)
    status.set_defaults(func=cmd_status)

    fit_cmd = subparsers.add_parser("fit", help="fit the scale and show it")
    add_store(fit_cmd, rater=True, target=True)
    fit_cmd.set_defaults(func=cmd_fit)

    cuts = subparsers.add_parser("cuts", help="set the three band cuts")
    add_store(cuts, rater=True, target=True)
    for name in CUT_ORDER:
        cuts.add_argument(
            f"--{name.value.replace('_', '-')}",
            required=True,
            metavar="ABOVE:BELOW",
            help=f"the two findings either side of the {name.value} boundary",
        )
    cuts.set_defaults(func=cmd_cuts)

    bands_cmd = subparsers.add_parser("bands", help="show each finding's band")
    add_store(bands_cmd, rater=True, target=True)
    bands_cmd.set_defaults(func=cmd_bands)

    export = subparsers.add_parser("export", help="write the severity file")
    add_store(export, rater=True, target=True)
    export.add_argument("--out", required=True, help="path to write")
    export.set_defaults(func=cmd_export)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result: int = args.func(args)
    except ComparativeJudgmentError as exc:
        # Named refusals reach the user as their message and a non-zero exit,
        # never as a traceback: every one of them is a deliberate decision this
        # tool made, not a crash.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())

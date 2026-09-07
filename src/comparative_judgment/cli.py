"""Command line entry points.

Every subcommand drives the session API and nothing reaches around it — setting
cuts and exporting included, not only the comparison loop. That is the same
discipline the terminal UI follows, and having two front ends over one interface
is what keeps the interface honest — a seam with a single consumer drifts toward
that consumer without anyone noticing. The scan in `test_constraints` covers both
front ends, derived from the package layout rather than listed, because a check
narrower than the rule it enforces is green while the rule is broken.

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

from comparative_judgment.core.errors import ComparativeJudgmentError
from comparative_judgment.core.models import CUT_ORDER, Cut
from comparative_judgment.core.session import DEFAULT_APPEARANCE_TARGET, Session

#: The location this project's own documentation and examples use. It is
#: gitignored so that following the examples cannot produce a file that quietly
#: wants committing — but it is a convention, never a fallback: omitting
#: --store is an error, not a prompt to use this.
CONVENTIONAL_STORE = ".cj-store"


def _session(args: argparse.Namespace, *, target: int = DEFAULT_APPEARANCE_TARGET) -> Session:
    return Session.open(
        Path(args.store), rater_id=args.rater or "unnamed", appearance_target=target
    )


def cmd_init(args: argparse.Namespace) -> int:
    session = Session.create(Path(args.store), rater_id="unnamed", force=args.force)
    print(f"created store at {session.store_path}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    accepting = args.accept_revisions or args.accept_removals
    if accepting and not args.rater:
        # D18's stated value is that who, when, which finding and which text are
        # all recoverable. A default silently costs the first of the four, on the
        # path most likely to be taken in a hurry.
        print(
            "--rater is required when accepting a revision or a removal: the "
            "acceptance is written into the log as an audit record, and an "
            "unattributed one answers three of its four questions",
            file=sys.stderr,
        )
        return 1
    session = _session(args)
    outcome = session.load(
        Path(args.findings),
        accept_revisions=args.accept_revisions,
        accept_removals=args.accept_removals,
    )

    # A judged finding whose text changed, or which has left the document, is
    # refused by default. Carrying the old judgments over is a decision a human
    # makes, not one the tool makes quietly -- and accepting it is written into
    # the append-only log so the acceptance is auditable rather than invisible.
    if not outcome.applied:
        if outcome.pending_revisions:
            print(
                "REFUSED: text has changed on finding(s) that were already judged.",
                file=sys.stderr,
            )
            for revision in outcome.pending_revisions:
                print(
                    f"  {revision.finding_id}: {revision.old_hash[:12]} -> "
                    f"{revision.new_hash[:12]}"
                    f"  ({revision.comparisons} comparison(s) made against the old text)",
                    file=sys.stderr,
                )
            print(file=sys.stderr)
            print(
                "Those judgments were made against wording nobody has compared since.",
                file=sys.stderr,
            )
            print(
                "Re-run with --accept-revisions to carry them over; the acceptance is",
                file=sys.stderr,
            )
            print(
                "recorded in the log with your rater id and the hashes it moved between.",
                file=sys.stderr,
            )
        if outcome.pending_removals:
            if outcome.pending_revisions:
                print(file=sys.stderr)
            print(
                "REFUSED: finding(s) that were already judged are gone from the document.",
                file=sys.stderr,
            )
            for removal in outcome.pending_removals:
                print(
                    f"  {removal.finding_id}: {removal.old_hash[:12]}"
                    f"  ({removal.comparisons} comparison(s) made against it)",
                    file=sys.stderr,
                )
            print(file=sys.stderr)
            print(
                "Those comparisons stay in the log, but the fit would stop knowing what",
                file=sys.stderr,
            )
            print(
                "they refer to -- so the partner's appearance count silently falls and",
                file=sys.stderr,
            )
            print(
                "judgments you made disappear from the result. Re-run with",
                file=sys.stderr,
            )
            print(
                "--accept-removals to record the removal in the log deliberately.",
                file=sys.stderr,
            )
        return 1

    for accepted in outcome.accepted_revisions:
        print(
            f"accepted revision: {accepted.finding_id} "
            f"({accepted.comparisons} prior comparison(s) carried over)"
        )
    for dropped in outcome.accepted_removals:
        print(
            f"accepted removal: {dropped.finding_id} "
            f"({dropped.comparisons} comparison(s) now refer to a finding not in the batch)"
        )

    print(f"admitted {outcome.admitted} finding(s)")
    if outcome.excluded_questions:
        # Reported, never silent: a rater who expected seventy and sees
        # sixty-three needs to know why, and "they were questions" is a very
        # different situation from "they were malformed".
        listed = ", ".join(outcome.excluded_questions)
        print(f"excluded {len(outcome.excluded_questions)} question-tier entr(ies): {listed}")
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

    if progress.blocked_reason:
        print()
        print(f"BLOCKED: {progress.blocked_reason}")

    parts = session.components()
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
    result = session.fit()
    print(f"converged in {result.iterations} iteration(s); {result.ties} tie(s) excluded")
    print(f"regularization lambda = {result.regularization}")
    print()
    print(f"{'finding':<16} {'theta':>9} {'app':>5} {'W':>4} {'L':>4} {'T':>4}")
    for estimate in result.ranked():
        print(
            f"{estimate.finding_id:<16} {estimate.theta:>9.4f} {estimate.appearances:>5} "
            f"{estimate.wins:>4} {estimate.losses:>4} {estimate.ties:>4}"
        )
    parts = session.components()
    if len(parts) > 1:
        print()
        print(f"WARNING: these values span {len(parts)} groups never compared against")
        print("  each other. Bradley-Terry estimates differences, so each group has its")
        print("  own arbitrary origin and the numbers above are not comparable across")
        print("  them. `bands` and `export` will refuse until a comparison joins them.")
        for group in parts:
            print(f"    - {len(group)} item(s): {', '.join(group[:6])}")
    return 0


def cmd_cuts(args: argparse.Namespace) -> int:
    """Set the three band cuts, as anchor pairs.

    The only absolute judgments the tool ever asks for, and there are exactly
    three of them regardless of how many findings there are.
    """
    session = _session(args, target=args.target)
    if len(session.estimates()) < 4:
        print("need at least four findings to place three cuts", file=sys.stderr)
        return 1

    notes = {
        CUT_ORDER[0]: args.critical_high_note,
        CUT_ORDER[1]: args.high_medium_note,
        CUT_ORDER[2]: args.medium_low_note,
    }
    pairs = [args.critical_high, args.high_medium, args.medium_low]
    cuts: list[Cut] = []
    for name, spec in zip(CUT_ORDER, pairs, strict=True):
        above, _, below = spec.partition(":")
        if not above or not below:
            print(f"--{name.value.replace('_', '-')} must read ABOVE:BELOW", file=sys.stderr)
            return 1
        cuts.append(Cut(name=name, above_id=above, below_id=below, calibration_note=notes[name]))

    values = session.set_cuts(cuts)
    print("cuts stored as anchor pairs (not as numbers -- a pairwise scale has no")
    print("absolute origin, so a stored threshold means something only relative to")
    print("the fit that produced it):")
    for cut, value in zip(cuts, values, strict=True):
        print(
            f"  {cut.name.value:<14} {cut.above_id} | {cut.below_id}   threshold now {value:+.4f}"
        )
        if cut.calibration_note.strip():
            print(f"    calibrated against: {cut.calibration_note}")
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
    path = Path(args.out)
    placement = session.export(path)
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
                default=DEFAULT_APPEARANCE_TARGET,
                help="appearances per item before the batch reports complete",
            )

    init = subparsers.add_parser("init", help="create a new store")
    init.add_argument("--store", required=True)
    init.add_argument(
        "--force",
        action="store_true",
        help="re-initialize an existing store; still refused once judgments exist",
    )
    init.set_defaults(func=cmd_init)

    load = subparsers.add_parser("load", help="read a YAML findings document")
    load.add_argument("--store", required=True)
    load.add_argument("--findings", required=True)
    load.add_argument("--rater", help="who is accepting any revision or removal")
    load.add_argument(
        "--accept-revisions",
        action="store_true",
        help="carry judgments over onto changed text (recorded in the log)",
    )
    load.add_argument(
        "--accept-removals",
        action="store_true",
        help="accept that a judged finding has left the document (recorded in the log)",
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
        cuts.add_argument(
            f"--{name.value.replace('_', '-')}-note",
            default="",
            metavar="TEXT",
            help=(
                f"what the {name.value} boundary was calibrated against. Required on "
                "the top cut: a pairwise scale has no origin, so an internally perfect "
                "ordering can sit a whole band too high"
            ),
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

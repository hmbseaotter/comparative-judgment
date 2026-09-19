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
from comparative_judgment.core.models import (
    CUT_ORDER,
    ComponentReport,
    Cut,
    CutSeparation,
    Diagnostics,
    Progress,
    ProposedBand,
)
from comparative_judgment.core.session import (
    DEFAULT_APPEARANCE_TARGET,
    REGION_COMPARISONS,
    Session,
)

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


def _listed(ids: tuple[str, ...], limit: int = 8) -> str:
    """Names, truncated, with the remainder counted rather than dropped."""
    shown = ", ".join(ids[:limit])
    return shown if len(ids) <= limit else f"{shown} (+{len(ids) - limit} more)"


def _print_separation(separation: Sequence[CutSeparation]) -> None:
    """Each cut's gap and the findings between its anchors (D34). Reported, never refused."""
    if not separation:
        return
    print()
    print("cut separation (findings strictly between each cut's anchors on this fit):")
    for cut in separation:
        between = _listed(cut.between) if cut.between else "none"
        print(
            f"  {cut.name.value:<14} {cut.above_id} | {cut.below_id}   "
            f"gap {cut.gap:.4f}   between: {between}"
        )


def _proposal(proposal: ProposedBand) -> str:
    """One proposal as `F-03  high -> medium`, naming both sides even when one is absent."""
    assigned = proposal.assigned.value if proposal.assigned else "unassigned"
    current = proposal.current.value if proposal.current else "no band"
    return f"{proposal.finding_id:<16} {assigned} -> {current}"


def _print_proposals(proposals: Sequence[ProposedBand]) -> None:
    """Bands the current fit places other than as assigned (D36). Export refuses until assigned."""
    if not proposals:
        return
    print()
    print("proposed bands (assigned -> current fit; `assign` fixes them, `export` refuses until")
    print("it does, and changing an assigned band needs --accept-rebanding):")
    for proposal in proposals:
        print(f"  {_proposal(proposal)}")


def _remaining(progress: Progress) -> str:
    """The estimate as a rater reads it: a bound says so, and a blocked batch has none (D40)."""
    if progress.comparisons_remaining is None:
        return "n/a (blocked)"
    if progress.remaining_is_lower_bound:
        return f"at least {progress.comparisons_remaining}"
    return str(progress.comparisons_remaining)


def cmd_status(args: argparse.Namespace) -> int:
    session = _session(args, target=args.target)
    progress = session.progress()
    print(f"admitted            {progress.admitted}")
    print(f"excluded questions  {progress.excluded_questions}")
    print(f"comparisons spent   {progress.comparisons_spent}  (ties: {progress.ties})")
    print(f"comparisons left    {_remaining(progress)}")
    print(f"appearance target   {progress.appearance_target}")
    print(
        f"appearances         min {progress.min_appearances}, mean {progress.mean_appearances:.2f}"
    )
    print(f"comparisons/item    {session.mean_comparisons_per_item():.2f}")
    print(f"mode                {'placing against cuts' if progress.placing else 'bootstrap'}")
    print(f"complete            {'yes' if progress.complete else 'no'}")
    if progress.placing:
        if progress.items_unplaced:
            print(f"unplaced            {_listed(progress.items_unplaced)}")
    elif progress.items_below_target:
        print(f"below target        {_listed(progress.items_below_target)}")
    _print_separation(progress.separation)
    _print_proposals(progress.proposals)

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
    _print_separation(placement.separation)
    _print_proposals(placement.proposals)
    return 0


def cmd_assign(args: argparse.Namespace) -> int:
    """Fix each banded finding's band, so a refit proposes rather than relabels (D36)."""
    if not args.rater:
        # The assignment is the record of who fixed each band. A default would
        # write "unnamed" into the one record whose purpose is to say who decided.
        print(
            "--rater is required to assign bands: the assignment is written into the "
            "log as the record of who fixed each band, and an unattributed one cannot "
            "say who decided",
            file=sys.stderr,
        )
        return 1
    session = Session.open(Path(args.store), rater_id=args.rater)
    outcome = session.assign(accept_rebanding=args.accept_rebanding)

    if outcome.refused:
        print(
            "REFUSED: the current fit moves band(s) that were already assigned.",
            file=sys.stderr,
        )
        for proposal in outcome.rebanded:
            print(f"  {_proposal(proposal)}", file=sys.stderr)
        if outcome.first_time:
            print(
                f"  (and {len(outcome.first_time)} finding(s) banded for the first time, "
                "which need no acceptance)",
                file=sys.stderr,
            )
        print(file=sys.stderr)
        print("A consumer may already cite the assigned bands. Re-run with", file=sys.stderr)
        print(
            "--accept-rebanding to accept the change -- recorded in the log with your",
            file=sys.stderr,
        )
        print(
            "rater id -- or add evidence until the fit agrees: compare further, or", file=sys.stderr
        )
        print("retract a mis-keyed judgment.", file=sys.stderr)
        return 1

    if not outcome.applied:
        print("every banded finding is assigned as the current fit places it; wrote nothing")
        return 0

    for proposal in (*outcome.first_time, *outcome.rebanded):
        print(f"{'re-banded' if proposal.assigned else 'assigned '}  {_proposal(proposal)}")
    if outcome.record is not None:
        print(f"recorded {len(outcome.record.bands)} band(s) under rater {outcome.record.rater_id}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    session = _session(args, target=args.target)
    path = Path(args.out)
    placement = session.export(path)
    print(f"wrote {len(placement.assignments)} severity assignment(s) to {path}")
    _print_separation(placement.separation)
    return 0


def _number(value: float | None, width: int = 7, places: int = 3) -> str:
    return f"{'-':>{width}}" if value is None else f"{value:>{width}.{places}f}"


def _print_components(components: Sequence[ComponentReport]) -> None:
    if len(components) <= 1:
        return
    print()
    print(f"WARNING: {len(components)} groups never compared against each other. Their scale")
    print("  values have independent origins; regions stay inside one group, and `bands`")
    print("  and `export` refuse until a comparison joins them.")
    for index, group in enumerate(components, start=1):
        print(
            f"    {index}. {len(group.members)} finding(s), {group.imported} imported, "
            f"{group.local} local: {_listed(group.members, 6)}"
        )


def _print_diagnostics(report: Diagnostics) -> None:
    print(f"comparison log      {report.comparison_log_hash[:16]}")
    print(f"anchor set          {report.anchor_set_version}")
    print(f"regularization      lambda = {report.regularization}")
    print(
        f"comparisons         {report.comparisons}  "
        f"(ties: {report.ties}, tie rate {report.tie_rate:.3f})"
    )
    if report.blocked_reason:
        print()
        print(f"BLOCKED: {report.blocked_reason}")

    print()
    print(
        f"soft regions, highest infit first (about {REGION_COMPARISONS} decided comparisons each;"
    )
    print("  read by rank -- a consistent rater scores well below 1 on sparse data):")
    if not report.regions:
        print("  none: no decided comparison yet")
    for region in report.regions:
        cuts = f"   straddles {', '.join(c.value for c in region.cuts)}" if region.cuts else ""
        group = f"   group {region.component}" if len(report.components) > 1 else ""
        print(
            f"  {region.rank:>3}. theta {region.low:+.3f} .. {region.high:+.3f}   "
            f"infit {region.infit:.3f}   outfit {region.outfit:.3f}   "
            f"{region.comparisons} compared, {region.ties} tied{cuts}{group}"
        )
        print(f"       {_listed(region.findings)}")

    print()
    print(
        f"{'finding':<16} {'theta':>8} {'se':>7} {'app':>4} {'inf':>4} {'ties':>4} "
        f"{'infit':>7} {'outfit':>7}"
    )
    for item in sorted(report.items, key=lambda i: (-i.theta, i.finding_id)):
        print(
            f"{item.finding_id:<16} {item.theta:>+8.4f} {item.se:>7.3f} {item.appearances:>4} "
            f"{item.informative:>4} {item.ties:>4} {_number(item.infit)} {_number(item.outfit)}"
        )

    _print_components(report.components)
    for bridge in report.anchor_sets:
        print()
        print(
            f"anchor set {bridge.anchor_set_version}: {bridge.new} finding(s) imported, "
            f"{bridge.shared} shared, {bridge.bridging} bridging comparison(s)"
        )


def cmd_diagnostics(args: argparse.Namespace) -> int:
    """Where the scale is soft and how precisely each finding sits (D37-D39, D44)."""
    session = _session(args, target=args.target)
    report = session.write_diagnostics(Path(args.out)) if args.out else session.diagnostics()
    _print_diagnostics(report)
    if args.out:
        print()
        print(f"wrote the report to {args.out}")
    return 0


def cmd_export_anchors(args: argparse.Namespace) -> int:
    session = _session(args, target=args.target)
    result = session.export_anchor_set(Path(args.out))
    print(
        f"wrote anchor set {result.version} to {args.out}: {result.findings} finding(s), "
        f"{result.comparisons} comparison(s) and the three cuts"
    )
    print("  the file holds findings text; keep it wherever you would keep the store")
    return 0


def cmd_import_anchors(args: argparse.Namespace) -> int:
    """Bring another store's anchors in, reporting how firmly they are tied to this one (D41)."""
    if not args.rater:
        print(
            "--rater is required to import an anchor set: the import is written into the "
            "log as a record of who brought another store's judgments into this one",
            file=sys.stderr,
        )
        return 1
    session = Session.open(Path(args.store), rater_id=args.rater)
    outcome = session.import_anchor_set(Path(args.anchors))
    if not outcome.applied:
        print(
            f"this store already holds everything anchor set {outcome.anchor_set_version} "
            f"carries ({len(outcome.shared_findings)} finding(s), {outcome.skipped} "
            "comparison(s)); wrote nothing"
        )
        return 0

    verb = "completed the interrupted import of" if outcome.resumed else "imported"
    print(f"{verb} anchor set {outcome.anchor_set_version}")
    print(
        f"  findings              {len(outcome.new_findings)} new, "
        f"{len(outcome.shared_findings)} already held"
    )
    print(
        f"  comparisons           {outcome.appended} appended, {outcome.skipped} already in the log"
    )
    print(f"  cuts                  {'adopted' if outcome.cuts_adopted else 'already the same'}")
    bridging = outcome.bridge.bridging if outcome.bridge else 0
    print(f"  bridging comparisons  {bridging}")
    print("    (decided comparisons between a finding the set added and one outside it;")
    print("     placing this store's own findings against the imported cuts adds them)")
    _print_components(outcome.components)
    print()
    print("No band was assigned: `cj bands` shows them as proposals, and `cj assign`")
    print("fixes them once placement is done.")
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

    assign = subparsers.add_parser(
        "assign", help="fix each finding's band, so a refit proposes rather than relabels"
    )
    assign.add_argument("--store", required=True, help="path to the store directory")
    assign.add_argument("--rater", help="who is fixing the bands (required; recorded in the log)")
    assign.add_argument(
        "--accept-rebanding",
        action="store_true",
        help="accept that the current fit changes an assigned band (recorded in the log)",
    )
    assign.set_defaults(func=cmd_assign)

    export = subparsers.add_parser("export", help="write the severity file")
    add_store(export, rater=True, target=True)
    export.add_argument("--out", required=True, help="path to write")
    export.set_defaults(func=cmd_export)

    diagnostics = subparsers.add_parser(
        "diagnostics", help="standard errors, misfit, tie rate and the scale's soft regions"
    )
    add_store(diagnostics, rater=True, target=True)
    diagnostics.add_argument("--out", help="also write the report as JSON to this path")
    diagnostics.set_defaults(func=cmd_diagnostics)

    export_anchors = subparsers.add_parser(
        "export-anchors", help="write this store's anchor set for another store to import"
    )
    add_store(export_anchors, rater=True, target=True)
    export_anchors.add_argument("--out", required=True, help="path to write")
    export_anchors.set_defaults(func=cmd_export_anchors)

    import_anchors = subparsers.add_parser(
        "import-anchors", help="bring another store's anchor set into this one"
    )
    import_anchors.add_argument("--store", required=True, help="path to the store directory")
    import_anchors.add_argument("--anchors", required=True, help="the anchor-set file to import")
    import_anchors.add_argument("--rater", help="who is importing (required; recorded in the log)")
    import_anchors.set_defaults(func=cmd_import_anchors)

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

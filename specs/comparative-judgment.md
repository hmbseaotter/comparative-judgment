# specification: comparative-judgment — severity scoring by pairwise comparison

## metadata
- Spec version: 0.12.0
- Status: DRAFT
- Last updated: 2026-09-18
- Author(s): Saso Gale
- Target type: feature (Python library + terminal UI + CLI)
- Build class: build-required
- Role: n/a — a scoring utility; no persona sharpens it. Its one stance-like property is that it never asks for a number, only for a comparison, and that is a requirement rather than a voice.
- Produced by: /specify @ f72b756
- Last swept: 2026-09-18 @ 0.11.0 @ D36 — trigger: ~8–10 accrued decisions, before publishing, or at phase completion, whichever comes first (change-based, never calendar-based)
- Artifacts land in: the `comparative-judgment` repository root
- Visibility: public (currently private, flipped when ready). The store path is always supplied by the caller — there is no implicit fallback — but the conventional location used by the documentation and examples is gitignored, so following the docs cannot cause an accidental commit. A general-purpose severity tool will be pointed at real production findings by someone, and a committing default is a trap.
- Decision record: `specs/comparative-judgment.decisions.md`
- Reproducibility: required, byte-identical — the same comparison log SHALL produce the same scale values and bands, and the same standard errors, which phase 2 computes (D39) and whose byte-identity is stated for two runs on one machine, since a linear-algebra call can differ between BLAS builds. This does not happen by accident: Bradley-Terry fitting is iterative, so it needs a fixed convergence tolerance, a fixed iteration cap, a deterministic item ordering, and deterministic tie-breaking.
- Timestamp standard: UTC ISO-8601 with `Z` suffix, second precision.
- Integrity: the comparison log is append-only (a retraction is a record, never a deletion). Every finding carries a content hash, so a load that would reinterpret judged text is refused until a human accepts it, and the acceptance is itself a record in the same log (D18); a judged finding *removed* from the document is refused the same way (D22). Every emitted severity file names the run id, the anchor-set version and the hash of the log it was computed from, so any result can be recomputed from the inputs it names. The run id is *derived* from those inputs rather than minted per export (D23), so it identifies the result and two exports over an unchanged history are byte-identical including it. The log's hash normalizes CRLF line endings to LF before hashing, and every file is written with LF, so the hash, the run id and the bytes do not depend on the platform that wrote the store (D35). A band, once assigned, is frozen by an assignment record in the same log (D36): a refit that would move it proposes the change rather than making it, and no severity file is written while a proposal stands unaccepted. The anchor-set version a severity file names is the most recent anchor set imported into the store, read from its import record, and `1` for a store that never imports (D41); an import is recorded in the same log, carrying the text of every finding it adds, so the log hash and the run id cover it.

## outcome
A rater assigns defensible severity to a set of findings by answering only *"which of these two is worse?"* — never by choosing a number on a scale. Measurable as: every finding in a batch receives a band; the number of **absolute** judgments required is exactly three (the band cuts) regardless of batch size; placing a finding against an established anchor set costs ~3 comparisons rather than ~log₂(n); re-running the fit over an unchanged comparison log reproduces identical scale values and bands byte for byte; and, from phase 2, the tool reports per finding a standard error and a misfit statistic locating where the rater's scale is soft — the half of this outcome D14 deferred, which the phase tags carried and this sentence did not.

## in scope
- [P1] **Core data model** — findings (stable id + content hash), comparisons (both item ids, winner or tie, rater id, session id, UTC timestamp), anchors, band cuts.
- [P1] **Append-only comparison log** with resumable sessions and retraction-as-record.
- [P1] **Regularized Bradley-Terry fit** by maximum likelihood (MM iteration), producing scale values with deterministic convergence.
- [P2] **Per-item standard errors** from the regularized Fisher information (D39) — needed by the diagnostics that consume them, not by band placement.
- [P1] **Simple heuristic pair selection** — pair items whose current estimates are close.
- [P1] **Band-cut setting** — exactly three absolute judgments, each made with findings visible on both sides.
- [P1] **Cut calibration** against the written consequence definitions, pinning at least the top cut to an absolute statement so the relative scale acquires an origin.
- [P1] **Band placement** against established cuts, targeting cut proximity rather than exact rank.
- [P1] **Frozen bands** — a band is fixed by an audited assignment, and a refit proposes a change to it for a rater to accept rather than relabeling a finding the consuming harness already cites (D36).
- [P1] **Terminal UI** — two findings side by side, single-keypress choice, undo, mark-as-tie, and appearance progress.
- [P2] **Session timer and comparisons-remaining estimate** in the UI (D40).
- [P1] **UI-agnostic session API** — `next_pair`, `record`, `undo`, `progress` and `estimates` for the comparison loop, and every other operation a front end performs, from loading findings to setting cuts, assigning bands and exporting (D24) — the only surface any front end may use.
- [P1] **YAML findings reader** — each entry carrying a stable `id`, `observation`, `evidence` (a list, so fragments stay separate), `consequence`, `detectable_by` and `tier` — and a **severity-file writer** keyed by finding id.
- [P2] **Diagnostics report** — per-item standard error, misfit statistics, tie rate, and the regions of the scale they identify as soft (D37, D38, D44).
- [P2] **Cross-corpus anchor import/export**, with a connectivity report naming under-bridged components (D41–D43).
- [P3] **Adaptive pair selection** and a confidence-based stopping rule.
- [P3] **Multi-rater analysis** — per-rater scales, inter-rater agreement, and localization of the pairs raters disagree on.
- [P4] **Web UI adapter** over the unchanged session API.

## out of scope (v1)
- **Judge-versus-human agreement measurement** — the third of the tool's three stated jobs, and unbuildable now: it consumes LLM judge verdicts that do not exist until the consuming harness reaches its own phase 4. Building it now means designing against an imagined input and testing against invented fixtures.
- **Rater drift detection** — needs longitudinal data that will not exist for months. Untestable in principle right now: you cannot test drift detection without drift.
- **Any LLM call, anywhere** — see the determinism boundary. Not a deferral; a permanent exclusion.
- **Automatic severity assignment** — the tool never proposes a band without a human comparison behind it. Its purpose is to make human judgment reliable, not to replace it.
- **Rubric or scoring-rule authoring** — the tool surfaces where a scale is ambiguous; writing the rule that resolves it is human work done elsewhere.
- **Coupling to any specific harness** — integration is by file format only. The tool never imports from, or is imported by, a consuming project.

## control surface
n/a (not an agent) — an interactive TUI plus non-interactive CLI subcommands. No autonomous loop and no self-directed action. Stop conditions: the rater exits (state persisted), the batch is fully placed, or the fit fails to converge within its iteration cap.

## triggers & scheduling
n/a (not an agent) — invoked on demand. Sessions are resumable, so a long bootstrap is expected to span several sittings rather than one.

## tools & permissions
n/a (not an agent). Bright lines that are nonetheless real:
- Filesystem: reads a findings file and a store directory; writes the store — its log, findings index, cuts and metadata — and severity files. Nothing outside paths given by the caller.
- Network: **none, ever.** The tool makes no outbound request of any kind.
- NEVER unattended: mutating the caller's findings file; deleting a comparison record; emitting a band for a finding with no comparison behind it; changing an assigned band without a rater's accepted assignment (D36).

## state & memory
Persistent by design — the comparison log *is* the product, and the ordering is derived from it rather than owned. Store layout: findings index (id, content hash, text) holding the findings document's findings, an append-only log of comparisons, retractions, accepted revisions and removals, band assignments and anchor-set imports, band-cut definitions, anchor-set version. Findings an import adds live in its record in the log rather than in the index, so a load cannot drop them, and the comparisons it brings keep the rater, session and timestamp they were made under and name the anchor set they came from (D41). No session keeps a cursor or a start and end record: the next pair is derived from the log, so a bootstrap resumes across days exactly where it stopped, and the session timer phase 2 adds is held in memory for the sitting and never written (D40). Nothing is ever deleted; a retraction appends a record marking a prior comparison withdrawn. Parsing is memoized per change to a file, checked against the file's size and modification time on every read (D45).

## model & cost routing + determinism boundary
- **Deterministic (plain code, NO LLM):** every single operation. Pair selection, the Bradley-Terry fit, standard errors, misfit statistics, band placement, cut calibration, tie handling, connectivity analysis, content hashing, log persistence, rendering data, file I/O.
- **Requires judgment (LLM):** **nothing.** This is a permanent architectural property, not a phase-one simplification. The tool exists to make *human* severity judgment reliable, and its output is the ground truth against which an LLM judge is later measured. Putting a model inside it would make the measuring instrument depend on the thing being measured — and the governing rule is that every layer of judging must bottom out in something deterministic or human, never in another unvalidated model.
- **Type & value discipline:** `mypy --strict` across the package; frozen dataclasses for every record type; `typing.Final` for constants; tuples for fixed collections. "type-check passes" is an acceptance criterion.
- **Cost guardrails:** the scarce resource is human comparisons, not tokens. The tool SHALL report comparisons spent, so a rater sees the cost as it accrues rather than discovering it afterwards; the *estimate* of comparisons remaining, which is what lets a rater decide when to stop, is phase 2's (D14, D40): a lower bound while bootstrapping, exact while placing.

## constraints
- Stack: Python 3.12+, `uv` with a committed `uv.lock` pinning exact versions, `pytest`, `mypy --strict`, `ruff`, `textual` for the TUI, `PyYAML`, `numpy` for the fit.
- The core package SHALL NOT import any UI library. Presentation lives in separate modules.
- Integration with any consuming project is by file format only — no shared imports in either direction.
- No new packages without flagging for approval first.

## prior decisions
- **Public tool; store path always explicit, conventional location gitignored**: the store holds findings text, which for other users may be real production defect descriptions. There is no implicit fallback path, so the tool never writes somewhere the caller did not name; the conventional documented location is gitignored so that following the examples cannot produce a file that quietly wants committing.
- **Bradley-Terry rather than a comparison sort**: a sort's efficiency *is* its transitivity assumption, so it structurally cannot observe the intransitivity the source design calls its most valuable output — it compares ~19% of pairs and skips exactly the third leg of any cycle. Worse, an intransitive rater still receives a confident total order that would differ under a different comparison order. Bradley-Terry assumes no total order, tolerates inconsistency natively, and reports it as graded misfit rather than a binary flag. Human effort is comparable (~10 appearances per item ≈ the same ~250 comparisons for 50 findings).
- **Band placement, not ranking**: severity output is "this is a High", never "this is the 287th-worst finding", so exact rank is precision that gets discarded. Placing against three cuts costs ~3 comparisons rather than ~9 — at 1,000 findings, ~3,000 comparisons instead of ~9,000.
- **Cold start still produces a total order on the first small batch**, because that is what allows the three cuts to be set with findings visible on either side, which is what makes those absolute judgments defensible.
- **Simple pairing now, adaptive selection later**: the Bradley-Terry core is small (MM iteration plus standard errors); adaptive information-maximizing selection is the expensive part and is a pure efficiency gain over a foundation that is already correct.
- **UI-agnostic core from the first commit**: a web adapter is expected later, so session state, pair selection and persistence live in the core, and the core returns structured presentation data rather than formatted output. Formatting in the core is the subtlest way to bake in a terminal assumption.
- **Rater identity recorded from day one**, with multi-rater analysis deferred: retrofitting the field later leaves every existing comparison unattributable, which is unfixable.
- **YAML findings in, separate severity file out**: YAML block scalars carry multi-line evidence that CSV quoting and Markdown table cells cannot. The tool never mutates the findings file, so provenance stays explicit and the tool works where it lacks write access.
- **The consuming harness uses YAML for its findings document** — settled, not assumed: that spec named no format until its own D22 fixed it to match this one. No format adapter is needed.
- **Ties are recorded, excluded from the fit, and reported as a rate**: forcing a winner on a genuinely equal pair manufactures a false signal, and the tie rate is itself a scale-softness measure. Fitting ties properly (Davidson's extension) is deferred.

## requirements

### ubiquitous (always active)
- The system SHALL [P1] store every comparison as an append-only record carrying both item identifiers, the outcome, the rater identifier, the session identifier and a UTC timestamp.
- The system SHALL [P1] identify every finding by a stable identifier together with a content hash of its text.
- The system SHALL [P1] contain no call to any language model, and SHALL declare no dependency that makes one.
- The system SHALL [P1] confine all business logic to a core package that imports no user-interface library.
- The system SHALL [P1] expose exactly one session interface to presentation layers, covering the whole tool rather than only the comparison loop: next-pair, record, undo, progress and estimates, and the loading, cut, band, assignment and export operations (D24).
- The system SHALL [P1] return structured presentation data from that interface, and SHALL NOT return preformatted display strings.
- The system SHALL [P1] pass `mypy --strict` with no ignored errors, declaring record types as frozen dataclasses and constants as `typing.Final`.
- The system SHALL [P1] assign a band to a finding only where at least one recorded comparison involves that finding.
- The system SHALL [P1] hold each finding's band as its most recent assignment record states it, and SHALL treat a band on the current fit that differs from it as a proposed revision rather than a change: anything that refits the scale or re-sets a cut can propose one, and only an accepted assignment makes one (D36).

### event-driven (WHEN — triggered by an action)
- WHEN [P1] the fit is run over an unchanged comparison log, the system SHALL produce identical scale values and bands, using a fixed convergence tolerance, a fixed iteration cap and a deterministic item ordering.
- WHEN [P2] standard errors are computed, they SHALL be reproducible under the same conditions as the scale values.
- WHEN [P2] standard errors are computed, the system SHALL take them from the regularized information — the negative Hessian of the penalized likelihood the fit maximizes, λ's virtual opponent included — SHALL report one for every finding, an unjudged finding's being the prior's alone, and SHALL state their byte-identity as holding across two runs on one machine (D39).
- WHEN [P1] the fit is run, the system SHALL apply a symmetric prior of λ pseudo-wins and λ pseudo-losses per item (λ = 0.5) against a virtual opponent at the scale origin, so that an item winning or losing all of its comparisons still receives a finite scale value, and SHALL state λ in its documentation rather than only in code.
- WHEN [P1] a rater records a comparison, the system SHALL persist it before presenting the next pair.
- WHEN [P1] a rater retracts a comparison, the system SHALL append a retraction record and SHALL NOT delete the original.
- WHEN [P1] a finding's content hash differs from the stored hash for an identifier that has already been judged, the system SHALL refuse the load, name each changed finding with its comparison count, and SHALL proceed only when revisions are explicitly accepted.
- WHEN [P1] a revision is explicitly accepted, the system SHALL append a record carrying the finding identifier, both content hashes, the comparison count at acceptance, the rater identifier and a timestamp.
- WHEN [P1] a findings document no longer contains an identifier that has already been judged, the system SHALL refuse the load, name each removed finding with its comparison count, and SHALL proceed only when removals are explicitly accepted.
- WHEN [P1] a removal is explicitly accepted, the system SHALL append a record carrying the finding identifier, its content hash, the comparison count, the rater identifier and a timestamp.
- WHEN [P1] a revision or a removal is accepted, the system SHALL require an explicit rater identifier and SHALL NOT substitute a default.
- WHEN [P1] a rater assigns bands, the system SHALL append one assignment record carrying the rater identifier, a UTC timestamp and, for every banded finding, its identifier, band and content hash; SHALL first list each band being assigned for the first time and each assigned band being changed; SHALL require an explicit rater identifier and SHALL NOT substitute a default; and SHALL refuse wherever band placement refuses. The record holds the whole set rather than the changes, so the latest assignment alone is the frozen state.
- WHEN [P1] an assignment would change a finding's assigned band, including to no band because every comparison involving it has been retracted, the system SHALL refuse unless re-banding is explicitly accepted by a flag of its own, and SHALL name each such finding with its assigned band and its band on the current fit. A first assignment needs no such flag, since it changes nothing a consumer has cited; the separate flag is D22's reasoning again, so that the more consequential acceptance is not reachable by habit.
- WHEN [P1] an assignment would assign no new band and change none, the system SHALL write nothing and SHALL say so: a record that changes nothing still changes the log's hash, and the run id with it.
- WHEN [P1] a removal is accepted for a finding that has an assigned band, the system SHALL let that assignment lapse without a separate re-banding acceptance, since the removal is itself an audited acceptance (D22).
- WHEN [P1] a revision is accepted for a finding that has an assigned band, the system SHALL keep the assignment: the judgments carry over the edit (D18), and the band changes only if the fit moves the finding and a rater accepts that.
- WHEN [P1] band cuts are set, the system SHALL require a non-empty calibration note on the most severe cut, naming the written consequence definition it was drawn against, and SHALL carry every note into the emitted severity file.
- WHEN [P1] a store is created where one already exists, the system SHALL refuse by name and SHALL change no file; WHERE re-initialization is explicitly forced, the system SHALL still refuse once any judgment has been recorded.
- WHEN [P1] the comparison graph over judged findings contains more than one connected component, the system SHALL name the components, SHALL refuse to assign bands or emit a severity file, and SHALL NOT report scale values as comparable across them. Findings with no comparisons are excluded from this check: they are isolated by definition, and counting them would report every part-way batch as disconnected.
- WHEN [P1] band cuts are set, the system SHALL require exactly three absolute judgments and SHALL display findings on both sides of each cut.
- WHEN [P1] a cut is stored, the system SHALL store it as the ordered pair of findings either side of it, and SHALL derive its threshold as the midpoint of those two findings' current scale values at each fit.
- WHEN [P1] every admitted item has reached the configured appearance target, the system SHALL report the cold-start phase complete, while permitting the rater to stop earlier or continue further.
- WHEN [P1] a severity file is written, the system SHALL record exactly these top-level fields: `schema_version`, `anchor_set_version`, `comparison_log_hash`, `run_id`, `calibration`, `cuts`, `severities` and `unplaced`. The field list is enumerated here rather than described, because the consuming harness reads provenance from it and a field added or renamed on this side is otherwise invisible on that one — the cross-repository scanner asserts the two documents name the same set.
- WHEN [P1] a severity file is written, the system SHALL record on **every row** the text it scored and the evidence behind it: `content_hash`, `appearances` and `informative`, alongside `id`, `severity` and `theta`. **A band on its own is a conclusion with its basis stripped off**, and the two halves fail differently. Without `content_hash` a finding edited after export keeps a band describing wording nobody compared, and only a person re-running `cj load` finds out — which is what happened on 2026-09-07, when a repository-wide spelling pass in the consuming project edited three judged findings and nothing noticed for four days. Without `appearances` and `informative` two rows that look identical can rest on very different evidence: a tie is a judgment this tool keeps and the fit excludes, so ten appearances with eight ties is a band placed on two results, and that is the row whose position moves furthest when one more comparison arrives. `schema_version` became `2` with this requirement, because the consumer's contract tolerates extra fields and therefore cannot tell a file that predates them from a tool that never wrote them.
- WHEN [P1] a session is resumed, the system SHALL restore the exact position in the batch and SHALL report comparisons already spent.
- WHEN [P2] anchors are imported from another store, the system SHALL report how many comparisons bridge the imported set to the existing one.
- WHEN [P2] anchors are imported, the system SHALL require an explicit rater identifier, SHALL validate the whole file and the merge before writing anything, and SHALL refuse by name, leaving the store byte-identical: a file that is malformed, whose version is not the hash of its content, whose finding text does not hash to its stated content hash, or that carries a question-tier finding; an anchor set already imported; an identifier this store holds with other text, has accepted the removal of, still names in live comparisons without holding, or excluded as a question; cuts that differ from the store's own; a merge that would invert a cut; and a merge that would leave any group of judged findings without an imported member, which the placement loop could never bridge (D41).
- WHEN [P2] anchors are imported, the system SHALL record the import in the log before the comparisons it brings, naming how many follow and carrying the text of every finding it adds; SHALL keep each comparison's rater, session and timestamp; SHALL skip a comparison the log already holds, matched by count rather than by membership; SHALL adopt the set's cuts only where the store has none; and SHALL assign no band — imported findings arrive as first-time proposals, and assigned bands the refit moves arrive as re-banding proposals (D41, D42).
- WHEN [P2] an import has been interrupted part-way, the system SHALL refuse every judgment, undo, load, cut, assignment and export by name until the same import is run again, and SHALL then complete it as an uninterrupted import would have.
- WHEN [P2] a findings document reuses the identifier of a finding an import added, with other text, the system SHALL refuse the load by name and SHALL offer no acceptance for it.
- WHEN [P2] a rater undoes a judgment, the system SHALL withdraw only a comparison made in this store, never one an import brought.

### state-driven (WHILE — true for the duration of a state)
- WHILE [P1] a comparison session is running, the system SHALL display comparisons spent.
- WHILE [P2] a comparison session is running, the system SHALL additionally display an estimate of comparisons remaining (D14) — while bootstrapping, half the appearances still owed against the target, rounded up and shown as a lower bound; while placing, each unplaced finding's shortfall against the placement quota, exactly; and none while blocked — and SHALL display the elapsed time of the sitting, measured from when the session opened and never written to the store (D40).
- WHILE [P1] placing a finding against established cuts, the system SHALL select pairs by proximity to a cut rather than by position in a full ordering.
- WHILE [P1] no cuts have been established, the system SHALL select pairs so as to produce a total order over the current batch, targeting a configured number of appearances per item (default 10) and reporting each item's progress against it.
- WHILE [P2] reporting diagnostics, the system SHALL emit per-item standard errors, misfit statistics and the tie rate, the misfit statistics being infit and outfit mean-squares of standardized residuals over decided comparisons, with ties excluded from them and reported beside them as each finding's tie count and tie rate (D37).
- WHILE [P2] reporting connectivity, the system SHALL give for each imported anchor set the findings it added, those it shared and its bridging comparisons — live decided comparisons between a finding it added and a finding outside it — and every connected component of judged findings with its imported and local members, and SHALL refuse nothing on that account (D43).
- WHILE [P1] a session is running, the system SHALL display comparisons spent and per-item appearance progress.
- WHILE [P1] reporting progress for a batch, the system SHALL report the mean comparisons spent per finding placed and the mean appearances per item, so the cost model's own estimates are measured in use rather than assumed.
- WHILE [P1] cuts are set and none is inverted, the system SHALL report for every cut, in progress, band placement and the severity file, the gap between its two anchors on the current fit and the banded findings strictly between them, most severe first, and SHALL NOT refuse on that account. A pair that inverts means nothing; a pair that drifts apart still means something, and the findings between it are banded by the midpoint rather than by any judgment against the boundary, which is how placing one finding re-banded three others in the consuming project (D34). How far apart is too far is left to the rater. `schema_version` is `3` from this requirement.
- WHILE [P1] any banded finding is unassigned, or its band on the current fit differs from its assigned band, the system SHALL report each such finding with its assigned band and its current one, in progress and band placement. A proposed revision is answered by accepting it or by adding evidence — more comparisons, or retracting a mis-keyed one — until the fit agrees; there is no rejecting one, because a band the fit contradicts cannot be exported (D36).

### unwanted behavior (IF — error handling)
- IF [P1] a rater marks a pair as too close to call, the system SHALL record a tie, SHALL exclude it from the fit, and SHALL count it toward the reported tie rate.
- IF [P1] the fit does not converge within its iteration cap, the system SHALL report non-convergence and SHALL NOT emit scale values.
- IF [P1] a fit inverts a cut's anchor pair, the system SHALL report that cut by name and SHALL NOT silently re-order it.
- IF [P1] a finding lacks `id`, `observation` or `consequence` text, the system SHALL refuse to admit it to a batch and SHALL name the finding.
- IF [P1] a comparison names the same finding on both sides, the system SHALL refuse it.
- IF [P1] a finding is admitted with no evidence fragment, the system SHALL refuse it and SHALL name it.
- IF [P1] a retraction names no live comparison, the system SHALL refuse it rather than appending a record that refers to nothing — the log's hash is published as provenance, so an inert record still changes the fingerprint of a history without changing what it says.
- IF [P1] a band cut names a finding with no comparisons, the system SHALL refuse the cut: the anchor's position is the prior's rather than a judgment's.
- IF [P1] any file the system reads is unreadable or syntactically malformed, the system SHALL raise a named refusal at the parse boundary naming the file, and SHALL NOT allow a parser's own exception to reach the caller.
- IF [P1] the system cannot write the severity file where it was asked to, the system SHALL report a named refusal.
- IF [P1] a severity file is requested while any banded finding is unassigned or its band on the current fit differs from its assigned band, the system SHALL refuse, SHALL name each such finding with its assigned band and its current one, and SHALL write nothing. So an exported band is always an assigned one, and never sits beside a `theta` that places it elsewhere, which the consuming harness refuses on load (its D171); the file's fields and `schema_version` are unchanged by this.
- IF [P1] an operation would fail validation, the system SHALL complete every check before writing anything, so that a refused operation leaves the store byte-identical.
- IF [P1] a finding's `tier` is `question`, the system SHALL exclude it from the batch, from the fit and from the anchor set, and SHALL report how many were excluded.
- IF [P1] a store is opened whose schema version is unrecognized, the system SHALL refuse to write and SHALL report the version mismatch.
- IF [P1] the caller supplies no store path, the system SHALL fail with a named error rather than defaulting to a path inside the repository.

### optional feature (WHERE — behind a flag / config)
- WHERE [P2] a diagnostics report is requested, the system SHALL identify the regions of the scale with the highest misfit: each decided comparison located at the mean scale value of its two findings, and within each connected component the comparisons cut in location order into regions of twenty, a trailing remainder merging into the region before it, each named by its span, its findings and any cut threshold it holds, and all ranked by infit (D38).
- WHERE [P2] a diagnostics report is requested with an output path, the system SHALL write it as its own JSON file, never into the severity file, naming the log hash and anchor-set version it was computed from (D44).
- WHERE [P2] an anchor set is exported, the system SHALL include the findings, their comparisons and the band-cut definitions needed to reuse it elsewhere: every judged finding with its text and content hash, every live comparison among them with its rater, session and timestamp, and the three cuts with their calibration notes; SHALL name the set by a hash of that content; and SHALL refuse wherever band placement refuses (D41).
- WHERE [P3] adaptive selection is enabled, the system SHALL choose the pair that most reduces estimated uncertainty and SHALL stop once an item's band is settled to the configured confidence.
- WHERE [P3] more than one rater has contributed, the system SHALL report per-rater scales and the pairs on which raters disagree.

### non-functional
- Security: the system SHALL make no network request, and SHALL write only beneath paths supplied by the caller. [P1]
- Privacy: the repository SHALL gitignore the conventional store location used by its documentation and examples, and the README SHALL state that findings text may be sensitive. [P1]
- Performance: a fit over 1,000 findings and 10,000 comparisons SHALL complete in under five seconds on a developer machine. [P1]
- Performance: a keypress in a running session — recording a judgment, deriving the next pair and reporting progress — SHALL take a median under 250 ms at 200 findings and about a thousand comparisons of realistic input: pairs near in rank, from a rater who is not perfectly consistent (D45). [P2]
- Error handling / observability: the system SHALL report comparisons spent and each item's progress against its target. [P1] The estimate of comparisons remaining and the elapsed session time — which are what let a rater judge fatigue against progress — are phase 2, because D14 moved both. [P2]
- Reproducibility: two fits over the same log SHALL be byte-identical, and so SHALL two severity files exported over an unchanged log, anchor set and set of cuts, run id included: the run id is derived from those inputs rather than minted (D23), so nothing is carved out as run metadata. [P1]

## failure & escalation
- Recoverable: a mis-keyed comparison is retracted by the rater and appended as a retraction; a session interrupted at any point resumes from its persisted position.
- Unrecoverable: unrecognized store schema, non-convergent fit, disconnected comparison graph where a cross-component comparison was requested, malformed findings file — each halts with a named cause and writes nothing.
- Stuck / uncertain: a rater who cannot decide marks a tie; this is a first-class outcome, not a failure, and feeds the tie rate.
- Escalation channel: non-zero exit code with a named cause on stderr; the log is left intact for inspection.

## acceptance criteria

> **No box here is ticked, and that is the honest state rather than a backlog.** Nothing computes
> these, so a tick would record what somebody believed at the moment they typed it. That decayed in
> both directions at once: thirteen were ticked while several unticked ones were implemented and
> tested, in a document whose own 0.6.0 entry cites *"a criterion ticked against something adjacent
> to it"* as the defect the boxes were added to fix (D30). What a criterion is worth is the test
> that asserts it, and the `**Rule**` line of the decision that introduced it names that test.

### happy path
- [ ] [P1] A cold-start batch of 50 findings reaches a total order, three cuts are set with three absolute judgments, and every finding receives a band.
- [ ] [P1] Placing a 51st finding against the established cuts costs no more than 4 comparisons, asserted by counting records.
- [ ] [P1] A session interrupted mid-batch and resumed continues at the same position with the same comparisons-spent count.
- [ ] [P1] A severity file is emitted keyed by finding id, carrying run id, anchor-set version and log hash.
- [ ] [P1] Two exports over an unchanged log, anchor set and set of cuts are byte-identical **including** the run id; changing any of the three changes it.
- [ ] [P1] A comparison log written with CRLF line endings and the same log written with LF name the same hash, and every file the tool writes asks for LF whichever platform writes it.
- [ ] [P1] The severity file carries the calibration note of every cut, and `cuts` refuses without one on the most severe cut.
- [ ] [P1] `assign` over a store with no assignment appends one record naming the rater and every banded finding's identifier, band and content hash, after which `export` succeeds; a second `assign` with nothing new or changed writes nothing, and the log hash and run id do not move.
- [ ] [P1] `assign` without a rater identifier is refused by name, and over a disconnected graph or an inverted cut it refuses as band placement does and writes nothing.
- [ ] [P1] The severity file's `cuts` names every cut's anchors, gap and the findings strictly between them, and each between-set equals what the file's own `theta` values place between that cut's anchors.
- [ ] [P1] `init` against an existing store refuses by name, changes no file, and the three cuts and the load summary both survive; `--force` re-initializes an unjudged store and still refuses a judged one.
- [ ] [P1] `cuts` naming an unknown finding, or a finding with no comparisons, leaves `cuts.json` byte-identical.
- [ ] [P1] Two `Store` handles opened on one store and appended to alternately produce strictly increasing, unique sequence numbers; retracting one leaves the other active.
- [ ] [P1] A judged finding removed from the document is refused by name with its comparison count and its judgments survive; an unjudged one may be removed freely; accepting appends a removal record and changes the log hash.
- [ ] [P1] `--accept-revisions` or `--accept-removals` without a rater identifier is refused by name.
- [ ] [P1] `bands` and `export` over a disconnected graph refuse by name and write nothing; `fit` and `status` name the components.
- [ ] [P1] A missing findings file, syntactically malformed YAML, a corrupt `meta.json` and a truncated log line each exit non-zero with a named refusal and no traceback.
- [ ] [P1] A retraction naming no live comparison, a repeated retraction, and a finding with empty evidence are each refused by name.
- [ ] [P1] With an inverted cut, the front end's progress pane names the cut and does not say "complete"; after cuts exist it does not report the appearance target as the finishing condition.
- [ ] [P1] With an unplaced item present, the reported mean-comparisons figure counts only findings that have been compared.
- [ ] [P1] The findings file is unchanged after a full scoring run, asserted by content hash before and after.
- [ ] [P2] A diagnostics report names per-item standard errors, misfit statistics and the tie rate.
- [ ] [P2] An exported anchor set imports into an empty store and reports its bridging-comparison count.
- [ ] [P1] A recorded comparison carries all six fields — both item ids, outcome, rater id, session id and UTC timestamp; the count of records missing any is zero.
- [ ] [P1] A running session displays comparisons spent and per-item appearance progress.
- [ ] [P2] A running session displays an estimate of comparisons remaining and elapsed session time.
- [ ] [P1] A completed batch reports mean comparisons per finding placed and mean appearances per item, and those figures are compared against the spec's ~3 and ~10 estimates.
- [ ] [P2] A diagnostics run names the specific scale regions with the highest misfit, not merely per-item values.
- [ ] [P2] An exported anchor set imported into an empty store reproduces the source's scale values bit for bit, adopts its cuts, and becomes the store's anchor-set version.
- [ ] [P2] Placing newcomers against imported anchors raises the reported bridging count by one per decided comparison.
- [ ] [P2] An import writes no band assignment: imported findings are first-time proposals, a later import that moves an assigned band proposes the change, and `export` refuses until `assign` accepts.
- [ ] [P3] Adaptive selection reaches the same bands as the phase-1 heuristic on the same batch using measurably fewer comparisons.
- [ ] [P3] With two raters present, per-rater scales are reported and the pairs they disagree on are listed.

### edge cases
- [ ] [P1] Two fits over an unchanged log produce byte-identical scale values and bands.
- [ ] [P2] Standard errors are byte-identical across two fits over an unchanged log.
- [ ] [P1] A comparison log built in a different insertion order, containing the same judgments, produces the same bands.
- [ ] [P1] A finding whose text is edited after being judged causes the load to be refused by name with its comparison count; an unjudged finding may change freely.
- [ ] [P1] Accepting a revision appends a record carrying both hashes and the rater identifier, and changes the comparison-log hash.
- [ ] [P1] A comparison naming the same finding on both sides is refused.
- [ ] [P1] A retracted comparison is absent from the fit while its record remains present in the log.
- [ ] [P1] A tie is recorded, excluded from the fit, and counted in the tie rate.
- [ ] [P1] A deliberately intransitive triad (A>B, B>C, C>A) is accepted without error rather than rejected, and leaves the items it involves indistinguishable on the scale.
- [ ] [P2] That same triad raises the misfit statistic for the items it involves.
- [ ] [P2] A stretch of the scale judged inconsistently ranks its region first in the diagnostics.
- [ ] [P2] An unjudged finding's standard error is the prior's alone, 2.0, and the information the standard errors invert equals a numerical Hessian of the fit's own objective.
- [ ] [P2] Every anchor-set import refusal leaves the store byte-identical.
- [ ] [P2] An import interrupted part-way blocks every write by name, and running it again completes it with the log an uninterrupted import would have written.
- [ ] [P2] A judgment recorded twice in one second travels in an anchor set as two judgments.
- [ ] [P2] Undo after an import withdraws only a comparison made in this store.
- [ ] [P2] The estimate of comparisons remaining never exceeds what a bootstrap goes on to spend, and while placing falls by one per comparison to zero.
- [ ] [P1] An item that wins every one of its comparisons receives a finite scale value, and the fit converges within its iteration cap rather than reaching it.
- [ ] [P1] A cut round-trips as an ordered pair of findings, and its threshold is recomputed from current scale values rather than stored as a number.
- [ ] [P1] A refit that inverts a cut's anchor pair reports that cut by name.
- [ ] [P1] A comparison that moves an assigned finding across a cut is reported as a proposed revision, with both bands, by `status` and `bands`; `export` then refuses naming it and writes nothing; `assign` without the re-banding flag refuses naming it; and `assign` with the flag appends a record and changes the log hash and run id.
- [ ] [P1] Re-setting a cut so that an assigned finding changes band is reported and refused exactly as a comparison that moves it.
- [ ] [P1] A finding whose every comparison is retracted after it was assigned is reported as a proposed revision to no band, and `assign` refuses it without the re-banding flag.
- [ ] [P1] A finding banded for the first time is assigned without the re-banding flag, and `export` refuses until it is.
- [ ] [P1] An accepted removal of an assigned finding needs no re-banding acceptance, and an accepted revision of one leaves its assignment standing.
- [ ] [P1] A cut whose anchors have a finding strictly between them is reported by name with that finding and the gap, in `status`, `bands` and the severity file, and nothing is refused; a finding level with an anchor is not counted as between.
- [ ] [P1] A batch reports per-item appearance progress and completes only when every admitted item reaches the configured target.
- [ ] [P1] Two batches with no bridging comparisons are reported as separate components, and no cross-component band comparison is emitted.
- [ ] [P1] A fit forced past its iteration cap reports non-convergence and emits no values.
- [ ] [P1] A finding missing `id`, observation or consequence text is refused by name.
- [ ] [P1] A findings file mixing `defect` and `question` tiers admits only the defects, excludes the questions from the fit and the anchor set, and reports the excluded count.
- [ ] [P1] A findings entry whose `evidence` holds three separate fragments is read with all three still distinct.
- [ ] [P1] A finding with no comparison recorded against it receives no band, and is reported as unplaced rather than defaulted.
- [ ] [P1] A session killed immediately after a keypress retains that comparison on restart, proving it was persisted before the next pair was presented.
- [ ] [P1] A store whose schema version is unrecognized refuses to write and names the version mismatch.

### constraint validation
- [ ] [P1] `mypy --strict` passes with zero errors and zero ignores; `ruff check` and `ruff format --check` pass.
- [ ] [P1] The core package imports no UI library, asserted by a static import scan.
- [ ] [P1] The session interface returns structured objects, asserted by a test that renders the same pair through two different formatters.
- [ ] [P1] No module makes a network call, asserted by a test that fails the run if a socket is opened.
- [ ] [P1] The repository contains no language-model dependency, asserted by scanning the lockfile.
- [ ] [P1] Invoking with no store path fails by name rather than writing inside the repository.
- [ ] [P1] `.gitignore` covers the conventional store location used by the documentation, present in the first commit.
- [ ] [P1] `uv.lock` pins exact resolved versions; no dependency is expressed only as a floor.
- [ ] [P1] A fit over 1,000 findings and 10,000 comparisons completes in under five seconds.
- [ ] [P2] A median keypress at 200 findings and about a thousand comparisons of realistic input stays under 250 ms.
- [ ] [P2] The diagnostics report and the anchor-set file ask for LF, and the severity file's fields and `schema_version` are unchanged.
- [ ] [P1] Record types are frozen dataclasses and module constants are `typing.Final`, asserted by a test that attempts mutation and expects failure.
- [ ] [P1] The TUI reaches the core only through the session interface, asserted by a scan for imports of core internals from presentation modules.
- [ ] [P1] The README states that findings text may be sensitive, that the store path is always explicit, and that the conventional location is gitignored.

---

## implementation phases

### phase 1 — scoring end to end
- Goal: a rater can cold-start a batch, set cuts, place findings, and emit severities — resumably, reproducibly, with no LLM anywhere.
- Includes (required floor): core data model and append-only comparison log; Bradley-Terry fit with deterministic convergence; the session API seam; band cuts, calibration and placement; YAML reader and severity writer; repo hygiene floor with the store gitignored from the first commit.
- Includes (chosen optional): the full TUI with undo, tie and progress — undo specifically, because a misfired key in a 250-comparison session otherwise becomes a silently wrong datum in the log everything downstream derives from; resumable sessions, since the bootstrap is expected to span sittings; and simple heuristic pairing, which is what makes the ~10-appearances estimate plausible rather than optimistic.
- ~~Deferred by choice: the connectivity check moves to phase 2.~~ **Retracted (D21).** It was in fact built in phase 1 — `core/graph.py` exists and `cj status` warned from the start — but only `status` asked, so `fit`, `bands` and `export` reported and wrote values across components never compared against each other. The `[P1]` tag the requirement always carried was the correct one; the deferral note was wrong when written and stale thereafter.
- **Reopened (D36), 2026-09-18.** Frozen bands were specified after phase 1 closed, and tagged `[P1]` because they guard the scoring path this phase built and the consuming harness needs them before its next judgment. They are not built yet, so phase 1 stays open until their criteria pass. **Built at 0.10.1, the same day**: the seven criteria are held by `tests/test_frozen_bands.py`, and the suite passes.
- Done when: its tagged acceptance criteria pass, and a 50-finding bootstrap completes with exactly three absolute judgments.

### phase 2 — diagnostics and portability
- Goal: the tool becomes a rubric-improvement instrument rather than only a scoring aid.
- Includes: per-item misfit and standard-error reporting, tie-rate reporting, soft-region identification, anchor import/export, connectivity reporting; and the two D14 moved here alongside the standard errors — the session timer and the comparisons-remaining estimate — which its acceptance criterion named while this list did not.
- Done when: an intransitive triad is visible as elevated misfit, and an anchor set moves between stores with its bridging count reported.
- **Built at 0.12.0, 2026-09-18**, to the nine decisions taken at its plan gate (D37–D45). Each `[P2]` criterion is held by the test its decision's Rule line names; the triad and the anchor-set move were also run through the command line and the terminal UI was driven headless, and the full suite, `mypy --strict`, `ruff check` and `ruff format --check` pass.

### phase 3 — efficiency and multiple raters
- Goal: the properties that matter at 1,000 findings and with more than one rater.
- Includes: adaptive pair selection, confidence-based stopping rule, per-rater scales, inter-rater agreement and disagreement localization.
- Done when: adaptive selection measurably reduces comparisons per finding against the phase-1 heuristic on the same batch.

### phase 4 — web adapter
- Goal: the second front end, proving the core is presentation-agnostic.
- Includes: a browser UI over the unchanged session API.
- Done when: the web adapter drives a full session with no change to the core package.

---

## assumptions
- [ ] Python 3.12+ with the harness's toolchain (`uv`, `pytest`, `mypy --strict`, `ruff`) is right here too — risk if wrong: a second toolchain to maintain for one author on one machine.
- [ ] `textual` is a suitable TUI library for side-by-side panes with keyboard input — risk if wrong: the TUI needs a different library, though the UI-agnostic core makes that a contained change.
- [ ] ~10 appearances per item gives adequate Bradley-Terry reliability at this scale — risk if wrong: more comparisons needed than budgeted, making the bootstrap longer than the ~250 estimate. **Mitigated**: the tool measures actual appearances per item, so the first bootstrap validates or refutes this rather than leaving it an untested claim. From phase 2 the diagnostics report each finding's standard error, which measures the precision this assumption is about directly (D39); nobody has yet read one off a real bootstrap.
- [ ] ~3 comparisons suffice to place a finding against three cuts — risk if wrong: placement costs more and the 1,000-finding projection of ~3,000 comparisons rises. **Mitigated**: the tool measures actual comparisons per finding placed.
- [ ] Ties are rare enough that excluding them from the fit does not bias the scale — risk if wrong: Davidson's extension is needed sooner than phase 3.
- [ ] A single rater is the near-term reality, with the rater field present but unexercised — risk if wrong: multi-rater analysis is needed before phase 3. From phase 2 an anchor-set import can bring another rater's comparisons into a store; they keep their rater ids and are pooled in the fit as one rater's, and separating them stays phase 3's (D41).
- [ ] Severity bands remain the four defect levels, with the `Question` tier outside the scale entirely — risk if wrong: the three-cut model and its "exactly three absolute judgments" claim both change.

---

## decisions made

*The compact what-and-why. The full reasoning, including the options each one beat and why, is in `specs/comparative-judgment.decisions.md`.*

**Settled before the build (D1–D13).** The tool is public with a gitignored conventional store path and no implicit fallback (D1, D9). Bradley-Terry replaces the source design's comparison sort, because a sort's efficiency *is* its transitivity assumption and it skips precisely the comparisons that would reveal a cycle (D2). A terminal UI now, a browser adapter later, with a session seam that keeps the first from becoming throwaway (D3). Rater identity is recorded from the first comparison, because retrofitting it leaves every earlier record unattributable (D4). Severity leaves in its own file keyed by id, never written back into the findings document (D5, D10). The cold-start phase ends at a configurable appearance target, default 10 (D11). A cut is stored as the *pair of findings* either side of it, not a threshold value, because a pairwise scale has no origin and a stored number means something only relative to the fit that produced it (D12). Regularization is λ = 0.5 pseudo-wins and pseudo-losses against a virtual opponent at the origin, without which the estimate diverges for any item that wins or loses all its comparisons — guaranteed at both ends of a severity scale (D13).

**Settled during the build (D14–D16).** Standard errors move to phase 2, since nothing in phase 1 consumes them (D14). The iteration cap is 200,000, set from measurement at n = 50, 75 and 100 after the original cap refused perfectly consistent input at n ≈ 60 (D16).

**Settled at the post-build sweep (D17–D18).** A changed finding is refused rather than silently reinterpreted or automatically forked, and accepting is audited in the same append-only log — so an acceptance changes the log hash, and a severity file naming that hash is tied to a history that includes it (D18).

**Settled after an independent audit of 0.5.0 (D19–D26).** The sequence number is re-read at every append rather than cached, because a cached counter desynchronizes the moment a second handle appends and a retraction then withdraws every record sharing that number (D19). Creating a store refuses to overwrite one, since the three band cuts are the only absolute judgments the tool asks for and were being destroyed by a command that exited zero (D20). Connectivity is enforced rather than merely reported: `bands` and `export` refuse across components never compared (D21). A judged finding removed from the document is refused exactly as a changed one is, with its own acceptance flag so the more consequential acceptance is not reachable by habit (D22). The run id is derived from the log hash, the anchor set and the cuts, so it identifies the *result* and needs no exclusion carved out of the byte-identity guarantee (D23). The session seam covers the whole tool rather than only the comparison loop, and the scan enforcing it is derived from the package layout rather than listed — the previous scan was green because its universe excluded the file that broke the rule (D24). The most severe cut requires a calibration note, because a pairwise ordering can be internally perfect while the whole set sits a band too high (D25). Every operation validates before it writes, and every parse boundary raises a named refusal (D26).

**Settled after phase 1 (D27–D35).** The repository uses US spelling, matching the consuming harness across the interface they share, and a guard holds it by the shapes of the other variety rather than by a list of the words one sweep happened to find (D27, D29). Once cuts exist, finishing a batch means every newcomer is placed, and that is a second field rather than a changed meaning for the first (D28). Acceptance criteria carry no ticks, because nothing computes them; a criterion is worth the test its decision's Rule names (D30). A push here that changes a specification asks the harness to run its interface scanner, since the scanner lives there (D31). Tests may reach past the session seam and front ends may not, and the check reads attributes as well as imports (D32). Every severity row carries the content hash it scored and how many comparisons, and how many decided ones, stand behind it (D33). Every cut reports how far apart its anchors have drifted and which findings lie between them, refusing nothing for it (D34). The log's hash normalizes line endings and every writer writes LF, so the hash and the run id do not depend on the platform that wrote the store (D35).

**Settled for the consuming project's OB-23 (D36).** A band is frozen by an assignment record in the append-only log, appended by `cj assign` under a rater's name, and a refit that would move it proposes the change instead of making it. Changing an assigned band needs its own flag, and `export` refuses while any band is unassigned or proposed for revision. It refuses rather than exporting the old band, because the consuming harness refuses a band that disagrees with its `theta`; so the severity file's shape is unchanged.

**Settled at the phase-2 plan gate (D37–D45).** Misfit is infit and outfit over decided comparisons, ties reported beside them rather than inside them; a triad on its own reads as the model's expectation, so it is seen against its consistent counterpart and inside a scale (D37). A soft region is twenty decided comparisons in scale order within one connected component, ranked by infit, so every region rests on the same evidence (D38). Standard errors come from the regularized information, the fit's own objective, so every finding has one and an unjudged finding's is the prior's alone (D39). The remaining-comparisons estimate is a lower bound while bootstrapping and exact while placing, and the timer measures the sitting in memory, since a start record would move the log hash (D40). An anchor set carries judged findings, their live comparisons and the cuts, named by a hash of its content; an import is recorded in the log before the comparisons it brings, refuses every collision by name, can be completed if interrupted, never lets `undo` retract another store's judgment, and refuses a merge the placement loop could not bridge (D41). An import assigns no band (D42). Bridging is reported per set and per component with no threshold (D43). Diagnostics are a subcommand writing their own file only where asked, leaving the severity file untouched (D44). Interactive latency is a requirement, met by parsing each file once per change rather than eight times per keypress (D45).

---

## emitted artifacts
n/a (build-required — see the build prompt)

---

## changelog
- 0.12.0 (2026-09-18): **phase 2 built — diagnostics and portability (D37–D45).**
  - **What was added:**
    - `cj diagnostics` reports each finding's standard error, from the regularized information; its infit and outfit over decided comparisons; the tie rate; and the scale's soft regions, twenty decided comparisons each within one component, ranked by infit. With `--out` it writes its own JSON file.
    - `cj export-anchors` and `cj import-anchors` move an anchor set between stores and report its bridging count. The import is recorded in the log before its comparisons, refuses every collision by name, can be completed if interrupted, and assigns no band.
    - `cj status` and the terminal UI show the comparisons-remaining estimate, and the UI shows the sitting's elapsed time.
    - Interactive latency is a requirement, and parsing each file once per change took a keypress at n = 200 from about 280 ms to 92.
  - **Requirements** were written for all of it, and twelve `[P2]` criteria were added. The anchor-set version is now settable, by import; a store that never imports keeps `1` and its run ids.
  - **Two latent defects were found and fixed on the way:**
    - The store split lines with `str.splitlines()`, so a finding whose text held U+2028 was cut in two and refused as invalid JSON.
    - An unknown enum value in a store file escaped as a `ValueError` rather than a named refusal.
  - **Unchanged:** the severity file's fields and `schema_version`, so neither enumerated list moves and the harness's scanner has nothing new to compare. A phase-1 build refuses a store holding an import record as an unknown log entry kind.
- 0.11.0 (2026-09-18): **sweep at D36**, the first since 0.7.0 @ D33. Six passages had been overtaken by later decisions and not updated where they stood. The reproducibility requirement still carved the run id out of byte-identity, which D23 made unnecessary and a happy-path criterion here contradicts. The session-interface requirement and its in-scope line listed only the comparison loop's five operations, which D24 widened to the whole tool. *State & memory* said sessions record their own start and end, which nothing does, since the next pair is derived from the log. *Tools & permissions* named a fit output the tool never writes. *Decisions made* stopped at D26. And D34, which declined the freeze as the larger change, carried no note that D36 built it. The bright lines gain changing an assigned band without a rater's accepted assignment. *Not checked* is refreshed and re-stamped, the decision record's provenance line reaches D36, and the frozen phase-1 build prompt no longer states a decision count of its own, the defect C-9 names. One redundancy is recorded rather than fixed, since a sweep keeps reorganization separate: displaying comparisons spent is required in three places. No behavior changed.
- 0.10.1 (2026-09-18): **D36 built.** `cj assign --rater` appends the assignment record, `--accept-rebanding` is required to change an assigned band, `status` and `bands` list every proposal as `assigned -> current`, and `export` refuses by name while any remains. No requirement changed; the version moves because D36's Rule line now names its tests, and the phase-1 and *Not checked* notes that said "not built" are superseded. Seven existing test sites exported without assigning, and now assign first. One of them, the check that the findings file is never written to, never asserted that its export succeeded: after this change it passed on an export that refused and wrote nothing, which is the guard this repository keeps finding green and blind. It now asserts the export, as does the byte-identity check beside it.
- 0.10.0 (2026-09-18): **a band, once assigned, is frozen, and a refit proposes rather than relabels (D36).** D2 promised this in its Consequences and nothing specified it; D34 declined to build it; the consuming harness registered it as its OB-23, owed here before the next comparison is recorded into its store. A new `assign` command appends an assignment record — the rater, a timestamp, and every banded finding's identifier, band and content hash — to the same append-only log as the comparisons, so the log hash and the run id cover it. A band the current fit places elsewhere is a proposed revision, reported by `status` and `bands`; changing an assigned band, including to no band, needs a re-banding flag of its own; and `export` refuses while any band is unassigned or proposed. Refusing is the only form a freeze can take here: the harness refuses a row whose band disagrees with its `theta` (its D171), so an old band cannot be exported beside a new fit. The severity file's fields and `schema_version` do not change. **Specified, not built**: phase 1 reopens on seven new criteria, and until they pass, a refit still relabels without saying so.
- 0.9.0 (2026-09-14): **the log's hash does not depend on the platform that wrote it (D35).** Every writer named no line ending, so a store written on Windows carried CRLF on every line, and `log_hash` hashed the log's raw bytes: the same judgments recorded on another platform named a different `comparison_log_hash` and `run_id`, which the consuming harness's phase-4 audit found on its own store. The hash now normalizes CRLF to LF before hashing, keeping the ordering and retractions it was chosen to cover, and every writer writes LF. A store written with CRLF names a different hash under this version than under 0.8.0, and re-exporting over an unchanged log moves `comparison_log_hash` and `run_id` and nothing else.
- 0.8.0 (2026-09-12): **a band cut says how far apart its anchors have drifted (D34).** `cj` refused a cut whose anchors had inverted and said nothing about one whose anchors had moved apart, though they are one defect at two magnitudes: in the consuming harness, placing one finding moved a cut's anchors from a 0.060 gap with nothing between them to a 1.4005 gap with seventeen findings inside, and three of those changed band with nothing reporting it. Every cut now reports its gap and the banded findings strictly between its anchors, in progress, band placement and the severity file, and nothing is refused on that account — one finding between anchors is ordinary drift, seventeen is not, and naming a line between those would be a threshold nobody chose. Severity schema 3 adds the top-level `cuts` field, computed from the rows the file exports so a consumer can re-derive each between-set from `theta` alone. D2's statement that a refit produces a proposed revision rather than silently relabeling was never made a requirement, and this change does not build it.
- 0.7.0 (2026-09-11): **a severity row says what it scored and how well determined it is (D33).** Schema 2 adds `content_hash`, `appearances` and `informative` to every row. Both halves exist because a band alone is a conclusion with its basis removed, and they fail differently. The hash is D18's, already computed and already refused against on load — it was simply never *exported*, so the consuming project could hold a severity file describing text it no longer had, detectable only by someone thinking to run `cj load`. That is not hypothetical: on 2026-09-07 a repository-wide spelling pass in the harness edited three judged findings and nothing saw it for four days, because the edit was a cross-cutting sweep and there was nobody working on severity to read the documentation that named the detector. The counts are the other half: ties are judgments this tool keeps and the fit excludes, so **ten appearances with eight ties is a band placed on two results** — which is precisely the row that moves furthest when one more comparison arrives, as the harness found when placing one new finding moved a cut anchor by forty-six times the median shift and silently re-banded three unrelated findings. `run_id` is unchanged by all of this, deriving from the log, the anchor set and the cuts rather than from the file's fields, so the provenance chain and the byte-identity guarantee both hold across the bump.
- 0.6.1 (2026-08-28): amended from the consuming harness's side. A pre-build audit of that project found that the severity file's **field set** was asserted by nobody: both specs described the file, neither enumerated it, and the harness named three of the five provenance fields. The requirement here now lists all seven top-level fields, and the cross-repository scanner gained a rule over them — so the agreement stops depending on two attentive readers, which is the condition D15 exists to remove. That audit also found a **fourth** member of the stale-cross-claim class D15 was built to close: D5's *Consequences* still said the harness "names no format", false since that project's D22. It survived the 0.4.0 repair, which fixed its twin in the assumptions block, because the scanner is pointed at the two specs and never at the two decision records. It is struck with a supersede note, and the scanner now takes a path list per side. Requirement changed, so this is a version bump rather than a sweep entry; no behavior changed and no test was altered.
- 0.6.0 (2026-08-28): an independent audit of 0.5.0 — a session that had written none of this code, read the spec, the record and the source, then ran the tool against constructed inputs — reported forty-one findings. Every executable one reproduced. Eight decisions follow (D19–D26): the sequence number is re-read rather than cached, because a cached counter let two handles write the same number and one retraction then withdrew both records; creating a store refuses to overwrite one, having silently destroyed all three band cuts and exited zero; connectivity is enforced rather than only reported by `status`; a judged finding *removed* from the document is refused like a changed one (D18's harm through the adjacent door); the run id — specified four times, implemented nowhere, and reported as passing — is derived rather than minted; the session seam covers the whole tool, and its scan is derived from the package layout rather than listed; the top cut requires a calibration note; every operation validates before writing and every parse boundary raises a named refusal. **Two corrections to earlier entries in this list.** The 0.5.0 line below says the cached counter "ended an O(n-squared) session cost" — one such cost ended; the session's own remains, and is now recorded in *Not checked* rather than implied away. The 0.4.1 line reports "31 of 31 criteria passed": it was 30, because the run-id criterion had never been implemented. That is the second time a criterion has been ticked against something adjacent to it, which is why the criteria above now carry checkboxes tied to named tests. **That last clause was false when written and stayed false**: the boxes were added to eight criteria and tied to nothing, since no tool reads them. By 2026-09-07 thirteen of sixty-seven were ticked, several implemented-and-tested criteria were not, and the ticks were removed rather than repaired (D30) — a box nothing computes is the claim that decays, which is what this sentence had just finished saying. CI added, so the three toolchain gates stop depending on someone remembering. Coverage 98%, 221 tests.
- 0.5.0 (2026-08-28): post-build sweep, sixteen findings. The content-hash requirement was replaced (D18) — specified, never implemented, and wrong as written: strict forking would orphan every judgment about a finding whenever a typo was fixed. A changed judged finding now refuses the load and accepting is recorded in the append-only log. Self-comparison refused. The sequence counter is cached, ending an O(n-squared) session cost. Dead code removed. Coverage 75% to 98%.
- 0.4.1 (2026-08-28): the intransitivity acceptance criterion split across the two phases it actually spans (D17). Its misfit half became unreachable in phase 1 when D14 moved standard errors to phase 2, and the wording did not follow. Phase 1 built and verified: 30 of 31 criteria passed before this split, 31 of 31 after.
- 0.4.0 (2026-08-28): phase 1 re-cut back toward the MVP the consuming project's sequencing decision assumed (D14) — standard errors, the session timer and the remaining-comparisons estimate move to phase 2. A cross-repository interface scanner added (D15). A stale assumption about the harness's findings format promoted to a settled prior decision.
- 0.3.0 (2026-08-28): three decisions taken at the phase-1 plan gate written in as requirements — the cold-start stopping condition (D11), cuts stored as anchor pairs rather than thresholds (D12), and a regularized Bradley-Terry fit (D13). D13 is not a refinement: without it the estimate diverges for any item winning or losing all its comparisons, which on a severity scale is guaranteed at both ends.
- 0.2.0 (2026-08-28): findings schema enumerated to match the consuming harness's spec, and `tier: question` entries excluded from batches, the fit and the anchor set (D10). Found by executing D9's own cross-repository check rather than by review.
- 0.1.0 (2026-08-28): initial draft. 7 decisions recorded; the source design's merge-sort bootstrap replaced with Bradley-Terry after the sort was found structurally unable to deliver the cycle capture that design calls its primary output.

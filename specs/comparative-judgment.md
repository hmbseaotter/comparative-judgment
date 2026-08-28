# specification: comparative-judgment — severity scoring by pairwise comparison

## metadata
- Spec version: 0.3.0
- Status: DRAFT
- Last updated: 2026-08-28
- Author(s): Saso Gale
- Target type: feature (Python library + terminal UI + CLI)
- Build class: build-required
- Role: n/a — a scoring utility; no persona sharpens it. Its one stance-like property is that it never asks for a number, only for a comparison, and that is a requirement rather than a voice.
- Produced by: /specify @ f72b756
- Last swept: 2026-08-28 @ 0.3.0 @ D13
- Artifacts land in: the `comparative-judgment` repository root
- Visibility: public (currently private, flipped when ready). The store path is always supplied by the caller — there is no implicit fallback — but the conventional location used by the documentation and examples is gitignored, so following the docs cannot cause an accidental commit. A general-purpose severity tool will be pointed at real production findings by someone, and a committing default is a trap.
- Decision record: `specs/comparative-judgment.decisions.md`
- Reproducibility: required, byte-identical — the same comparison log SHALL produce the same scale values, standard errors and bands. This does not happen by accident: Bradley-Terry fitting is iterative, so it needs a fixed convergence tolerance, a fixed iteration cap, a deterministic item ordering, and deterministic tie-breaking.
- Timestamp standard: UTC ISO-8601 with `Z` suffix, second precision.
- Integrity: the comparison log is append-only (a retraction is a record, never a deletion). Every finding carries a content hash, so revised text becomes a new item and old comparisons stay bound to the version actually judged. Every emitted severity file names the run id, the anchor-set version and the hash of the log it was computed from, so any result can be recomputed from the inputs it names.

## outcome
A rater assigns defensible severity to a set of findings by answering only *"which of these two is worse?"* — never by choosing a number on a scale. Measurable as: every finding in a batch receives a band; the number of **absolute** judgments required is exactly three (the band cuts) regardless of batch size; placing a finding against an established anchor set costs ~3 comparisons rather than ~log₂(n); re-running the fit over an unchanged comparison log reproduces identical scale values, standard errors and bands byte for byte; and the tool reports, per finding, a standard error and a misfit statistic locating where the rater's scale is soft.

## in scope
- [P1] **Core data model** — findings (stable id + content hash), comparisons (both item ids, winner or tie, rater id, session id, UTC timestamp), anchors, band cuts.
- [P1] **Append-only comparison log** with resumable sessions and retraction-as-record.
- [P1] **Bradley-Terry fit** by maximum likelihood (MM iteration), with per-item standard errors and deterministic convergence.
- [P1] **Simple heuristic pair selection** — pair items whose current estimates are close.
- [P1] **Band-cut setting** — exactly three absolute judgments, each made with findings visible on both sides.
- [P1] **Cut calibration** against the written consequence definitions, pinning at least the top cut to an absolute statement so the relative scale acquires an origin.
- [P1] **Band placement** against established cuts, targeting cut proximity rather than exact rank.
- [P1] **Terminal UI** — two findings side by side, single-keypress choice, undo, mark-as-tie, progress, session timer.
- [P1] **UI-agnostic session API** — `next_pair`, `record`, `undo`, `progress`, `estimates` — the only surface any front end may use.
- [P1] **YAML findings reader** — each entry carrying a stable `id`, `observation`, `evidence` (a list, so fragments stay separate), `consequence`, `detectable_by` and `tier` — and a **severity-file writer** keyed by finding id.
- [P2] **Diagnostics report** — per-item standard error, misfit statistics, tie rate, and the regions of the scale they identify as soft.
- [P2] **Cross-corpus anchor import/export**, with a connectivity report naming under-bridged components.
- [P3] **Adaptive pair selection** and a confidence-based stopping rule.
- [P3] **Multi-rater analysis** — per-rater scales, inter-rater agreement, and localisation of the pairs raters disagree on.
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
- Filesystem: reads a findings file and a store directory; writes the log, the fit output and severity files. Nothing outside paths given by the caller.
- Network: **none, ever.** The tool makes no outbound request of any kind.
- NEVER unattended: mutating the caller's findings file; deleting a comparison record; emitting a band for a finding with no comparison behind it.

## state & memory
Persistent by design — the comparison log *is* the product, and the ordering is derived from it rather than owned. Store layout: findings index (id, content hash, text), append-only comparison log, band-cut definitions, anchor-set version. Sessions record their own start/end so a bootstrap can be resumed across days. Nothing is ever deleted; a retraction appends a record marking a prior comparison withdrawn.

## model & cost routing + determinism boundary
- **Deterministic (plain code, NO LLM):** every single operation. Pair selection, the Bradley-Terry fit, standard errors, misfit statistics, band placement, cut calibration, tie handling, connectivity analysis, content hashing, log persistence, rendering data, file I/O.
- **Requires judgment (LLM):** **nothing.** This is a permanent architectural property, not a phase-one simplification. The tool exists to make *human* severity judgment reliable, and its output is the ground truth against which an LLM judge is later measured. Putting a model inside it would make the measuring instrument depend on the thing being measured — and the governing rule is that every layer of judging must bottom out in something deterministic or human, never in another unvalidated model.
- **Type & value discipline:** `mypy --strict` across the package; frozen dataclasses for every record type; `typing.Final` for constants; tuples for fixed collections. "type-check passes" is an acceptance criterion.
- **Cost guardrails:** the scarce resource is human comparisons, not tokens. The tool SHALL report comparisons spent and estimated comparisons remaining, so a rater can decide when to stop rather than discovering the cost afterwards.

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
- **Simple pairing now, adaptive selection later**: the Bradley-Terry core is small (MM iteration plus standard errors); adaptive information-maximising selection is the expensive part and is a pure efficiency gain over a foundation that is already correct.
- **UI-agnostic core from the first commit**: a web adapter is expected later, so session state, pair selection and persistence live in the core, and the core returns structured presentation data rather than formatted output. Formatting in the core is the subtlest way to bake in a terminal assumption.
- **Rater identity recorded from day one**, with multi-rater analysis deferred: retrofitting the field later leaves every existing comparison unattributable, which is unfixable.
- **YAML findings in, separate severity file out**: YAML block scalars carry multi-line evidence that CSV quoting and Markdown table cells cannot. The tool never mutates the findings file, so provenance stays explicit and the tool works where it lacks write access.
- **Ties are recorded, excluded from the fit, and reported as a rate**: forcing a winner on a genuinely equal pair manufactures a false signal, and the tie rate is itself a scale-softness measure. Fitting ties properly (Davidson's extension) is deferred.

## requirements

### ubiquitous (always active)
- The system SHALL [P1] store every comparison as an append-only record carrying both item identifiers, the outcome, the rater identifier, the session identifier and a UTC timestamp.
- The system SHALL [P1] identify every finding by a stable identifier together with a content hash of its text.
- The system SHALL [P1] contain no call to any language model, and SHALL declare no dependency that makes one.
- The system SHALL [P1] confine all business logic to a core package that imports no user-interface library.
- The system SHALL [P1] expose exactly one session interface to presentation layers, providing next-pair, record, undo, progress and estimates.
- The system SHALL [P1] return structured presentation data from that interface, and SHALL NOT return preformatted display strings.
- The system SHALL [P1] pass `mypy --strict` with no ignored errors, declaring record types as frozen dataclasses and constants as `typing.Final`.
- The system SHALL [P1] assign a band to a finding only where at least one recorded comparison involves that finding.

### event-driven (WHEN — triggered by an action)
- WHEN [P1] the fit is run over an unchanged comparison log, the system SHALL produce identical scale values, standard errors and bands, using a fixed convergence tolerance, a fixed iteration cap and a deterministic item ordering.
- WHEN [P1] the fit is run, the system SHALL apply a symmetric prior of λ pseudo-wins and λ pseudo-losses per item (λ = 0.5) against a virtual opponent at the scale origin, so that an item winning or losing all of its comparisons still receives a finite scale value, and SHALL state λ in its documentation rather than only in code.
- WHEN [P1] a rater records a comparison, the system SHALL persist it before presenting the next pair.
- WHEN [P1] a rater retracts a comparison, the system SHALL append a retraction record and SHALL NOT delete the original.
- WHEN [P1] a finding's content hash differs from the stored hash for its identifier, the system SHALL create a new item and SHALL retain prior comparisons against the previous version.
- WHEN [P1] the comparison graph contains more than one connected component, the system SHALL name the components and SHALL NOT report scale values as comparable across them.
- WHEN [P1] band cuts are set, the system SHALL require exactly three absolute judgments and SHALL display findings on both sides of each cut.
- WHEN [P1] a cut is stored, the system SHALL store it as the ordered pair of findings either side of it, and SHALL derive its threshold as the midpoint of those two findings' current scale values at each fit.
- WHEN [P1] every admitted item has reached the configured appearance target, the system SHALL report the cold-start phase complete, while permitting the rater to stop earlier or continue further.
- WHEN [P1] a severity file is written, the system SHALL record the run identifier, the anchor-set version and the hash of the comparison log it was computed from.
- WHEN [P1] a session is resumed, the system SHALL restore the exact position in the batch and SHALL report comparisons already spent.
- WHEN [P2] anchors are imported from another store, the system SHALL report how many comparisons bridge the imported set to the existing one.

### state-driven (WHILE — true for the duration of a state)
- WHILE [P1] a comparison session is running, the system SHALL display comparisons spent and an estimate of comparisons remaining.
- WHILE [P1] placing a finding against established cuts, the system SHALL select pairs by proximity to a cut rather than by position in a full ordering.
- WHILE [P1] no cuts have been established, the system SHALL select pairs so as to produce a total order over the current batch, targeting a configured number of appearances per item (default 10) and reporting each item's progress against it.
- WHILE [P2] reporting diagnostics, the system SHALL emit per-item standard errors, misfit statistics and the tie rate.
- WHILE [P1] reporting progress for a batch, the system SHALL report the mean comparisons spent per finding placed and the mean appearances per item, so the cost model's own estimates are measured in use rather than assumed.

### unwanted behavior (IF — error handling)
- IF [P1] a rater marks a pair as too close to call, the system SHALL record a tie, SHALL exclude it from the fit, and SHALL count it toward the reported tie rate.
- IF [P1] the fit does not converge within its iteration cap, the system SHALL report non-convergence and SHALL NOT emit scale values.
- IF [P1] a fit inverts a cut's anchor pair, the system SHALL report that cut by name and SHALL NOT silently re-order it.
- IF [P1] a finding lacks `id`, `observation` or `consequence` text, the system SHALL refuse to admit it to a batch and SHALL name the finding.
- IF [P1] a finding's `tier` is `question`, the system SHALL exclude it from the batch, from the fit and from the anchor set, and SHALL report how many were excluded.
- IF [P1] a store is opened whose schema version is unrecognised, the system SHALL refuse to write and SHALL report the version mismatch.
- IF [P1] the caller supplies no store path, the system SHALL fail with a named error rather than defaulting to a path inside the repository.

### optional feature (WHERE — behind a flag / config)
- WHERE [P2] a diagnostics report is requested, the system SHALL identify the regions of the scale with the highest misfit.
- WHERE [P2] an anchor set is exported, the system SHALL include the findings, their comparisons and the band-cut definitions needed to reuse it elsewhere.
- WHERE [P3] adaptive selection is enabled, the system SHALL choose the pair that most reduces estimated uncertainty and SHALL stop once an item's band is settled to the configured confidence.
- WHERE [P3] more than one rater has contributed, the system SHALL report per-rater scales and the pairs on which raters disagree.

### non-functional
- Security: the system SHALL make no network request, and SHALL write only beneath paths supplied by the caller. [P1]
- Privacy: the repository SHALL gitignore the conventional store location used by its documentation and examples, and the README SHALL state that findings text may be sensitive. [P1]
- Performance: a fit over 1,000 findings and 10,000 comparisons SHALL complete in under five seconds on a developer machine. [P1]
- Error handling / observability: the system SHALL report comparisons spent, estimated remaining, and elapsed session time, so a rater can judge fatigue against progress. [P1]
- Reproducibility: two fits over the same log SHALL be byte-identical, excluding a run-metadata envelope holding exactly the run id and timestamps. [P1]

## failure & escalation
- Recoverable: a mis-keyed comparison is retracted by the rater and appended as a retraction; a session interrupted at any point resumes from its persisted position.
- Unrecoverable: unrecognised store schema, non-convergent fit, disconnected comparison graph where a cross-component comparison was requested, malformed findings file — each halts with a named cause and writes nothing.
- Stuck / uncertain: a rater who cannot decide marks a tie; this is a first-class outcome, not a failure, and feeds the tie rate.
- Escalation channel: non-zero exit code with a named cause on stderr; the log is left intact for inspection.

## acceptance criteria

### happy path
- [ ] [P1] A cold-start batch of 50 findings reaches a total order, three cuts are set with three absolute judgments, and every finding receives a band.
- [ ] [P1] Placing a 51st finding against the established cuts costs no more than 4 comparisons, asserted by counting records.
- [ ] [P1] A session interrupted mid-batch and resumed continues at the same position with the same comparisons-spent count.
- [ ] [P1] A severity file is emitted keyed by finding id, carrying run id, anchor-set version and log hash.
- [ ] [P1] The findings file is unchanged after a full scoring run, asserted by content hash before and after.
- [ ] [P2] A diagnostics report names per-item standard errors, misfit statistics and the tie rate.
- [ ] [P2] An exported anchor set imports into an empty store and reports its bridging-comparison count.
- [ ] [P1] A recorded comparison carries all six fields — both item ids, outcome, rater id, session id and UTC timestamp; the count of records missing any is zero.
- [ ] [P1] A running session displays comparisons spent, an estimate remaining, and elapsed session time.
- [ ] [P1] A completed batch reports mean comparisons per finding placed and mean appearances per item, and those figures are compared against the spec's ~3 and ~10 estimates.
- [ ] [P2] A diagnostics run names the specific scale regions with the highest misfit, not merely per-item values.
- [ ] [P3] Adaptive selection reaches the same bands as the phase-1 heuristic on the same batch using measurably fewer comparisons.
- [ ] [P3] With two raters present, per-rater scales are reported and the pairs they disagree on are listed.

### edge cases
- [ ] [P1] Two fits over an unchanged log produce byte-identical scale values, standard errors and bands.
- [ ] [P1] A comparison log built in a different insertion order, containing the same judgments, produces the same bands.
- [ ] [P1] A finding whose text is edited produces a new item, and comparisons made against the previous text remain attached to it.
- [ ] [P1] A retracted comparison is absent from the fit while its record remains present in the log.
- [ ] [P1] A tie is recorded, excluded from the fit, and counted in the tie rate.
- [ ] [P1] A deliberately intransitive triad (A>B, B>C, C>A) is accepted without error, and raises the misfit statistic for those items.
- [ ] [P1] An item that wins every one of its comparisons receives a finite scale value, and the fit converges within its iteration cap rather than reaching it.
- [ ] [P1] A cut round-trips as an ordered pair of findings, and its threshold is recomputed from current scale values rather than stored as a number.
- [ ] [P1] A refit that inverts a cut's anchor pair reports that cut by name.
- [ ] [P1] A batch reports per-item appearance progress and completes only when every admitted item reaches the configured target.
- [ ] [P1] Two batches with no bridging comparisons are reported as separate components, and no cross-component band comparison is emitted.
- [ ] [P1] A fit forced past its iteration cap reports non-convergence and emits no values.
- [ ] [P1] A finding missing `id`, observation or consequence text is refused by name.
- [ ] [P1] A findings file mixing `defect` and `question` tiers admits only the defects, excludes the questions from the fit and the anchor set, and reports the excluded count.
- [ ] [P1] A findings entry whose `evidence` holds three separate fragments is read with all three still distinct.
- [ ] [P1] A finding with no comparison recorded against it receives no band, and is reported as unplaced rather than defaulted.
- [ ] [P1] A session killed immediately after a keypress retains that comparison on restart, proving it was persisted before the next pair was presented.
- [ ] [P1] A store whose schema version is unrecognised refuses to write and names the version mismatch.

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
- [ ] [P1] Record types are frozen dataclasses and module constants are `typing.Final`, asserted by a test that attempts mutation and expects failure.
- [ ] [P1] The TUI reaches the core only through the session interface, asserted by a scan for imports of core internals from presentation modules.
- [ ] [P1] The README states that findings text may be sensitive, that the store path is always explicit, and that the conventional location is gitignored.

---

## implementation phases

### phase 1 — scoring end to end
- Goal: a rater can cold-start a batch, set cuts, place findings, and emit severities — resumably, reproducibly, with no LLM anywhere.
- Includes (required floor): core data model and append-only comparison log; Bradley-Terry fit with deterministic convergence and standard errors; the session API seam; band cuts, calibration and placement; YAML reader and severity writer; repo hygiene floor with the store gitignored from the first commit.
- Includes (chosen optional): the full TUI with undo, tie, progress and session timer — undo specifically, because a misfired key in a 250-comparison session otherwise becomes a silently wrong datum in the log everything downstream derives from; resumable sessions, since the bootstrap is expected to span sittings; and simple heuristic pairing, which is what makes the ~10-appearances estimate plausible rather than optimistic.
- Deferred by choice: the connectivity check moves to phase 2 — phase 1 already refuses cross-component comparisons, and with a single batch connected by construction the diagnostic explaining why has nothing to report.
- Done when: its tagged acceptance criteria pass, and a 50-finding bootstrap completes with exactly three absolute judgments.

### phase 2 — diagnostics and portability
- Goal: the tool becomes a rubric-improvement instrument rather than only a scoring aid.
- Includes: per-item misfit and standard-error reporting, tie-rate reporting, soft-region identification, anchor import/export, connectivity reporting.
- Done when: an intransitive triad is visible as elevated misfit, and an anchor set moves between stores with its bridging count reported.

### phase 3 — efficiency and multiple raters
- Goal: the properties that matter at 1,000 findings and with more than one rater.
- Includes: adaptive pair selection, confidence-based stopping rule, per-rater scales, inter-rater agreement and disagreement localisation.
- Done when: adaptive selection measurably reduces comparisons per finding against the phase-1 heuristic on the same batch.

### phase 4 — web adapter
- Goal: the second front end, proving the core is presentation-agnostic.
- Includes: a browser UI over the unchanged session API.
- Done when: the web adapter drives a full session with no change to the core package.

---

## assumptions
- [ ] Python 3.12+ with the harness's toolchain (`uv`, `pytest`, `mypy --strict`, `ruff`) is right here too — risk if wrong: a second toolchain to maintain for one author on one machine.
- [ ] `textual` is a suitable TUI library for side-by-side panes with keyboard input — risk if wrong: the TUI needs a different library, though the UI-agnostic core makes that a contained change.
- [ ] ~10 appearances per item gives adequate Bradley-Terry reliability at this scale — risk if wrong: more comparisons needed than budgeted, making the bootstrap longer than the ~250 estimate. **Mitigated**: the tool measures actual appearances per item, so the first bootstrap validates or refutes this rather than leaving it an untested claim.
- [ ] ~3 comparisons suffice to place a finding against three cuts — risk if wrong: placement costs more and the 1,000-finding projection of ~3,000 comparisons rises. **Mitigated**: the tool measures actual comparisons per finding placed.
- [ ] The consuming harness will adopt YAML for its findings document — risk if wrong: a format adapter is needed, and the harness spec (which currently names no format) must be amended either way.
- [ ] Ties are rare enough that excluding them from the fit does not bias the scale — risk if wrong: Davidson's extension is needed sooner than phase 3.
- [ ] A single rater is the near-term reality, with the rater field present but unexercised — risk if wrong: multi-rater analysis is needed before phase 3.
- [ ] Severity bands remain the four defect levels, with the `Question` tier outside the scale entirely — risk if wrong: the three-cut model and its "exactly three absolute judgments" claim both change.

---

## decisions made
- [agent fills in during build]

---

## emitted artifacts
n/a (build-required — see the build prompt)

---

## changelog
- 0.3.0 (2026-08-28): three decisions taken at the phase-1 plan gate written in as requirements — the cold-start stopping condition (D11), cuts stored as anchor pairs rather than thresholds (D12), and a regularised Bradley-Terry fit (D13). D13 is not a refinement: without it the estimate diverges for any item winning or losing all its comparisons, which on a severity scale is guaranteed at both ends.
- 0.2.0 (2026-08-28): findings schema enumerated to match the consuming harness's spec, and `tier: question` entries excluded from batches, the fit and the anchor set (D10). Found by executing D9's own cross-repository check rather than by review.
- 0.1.0 (2026-08-28): initial draft. 7 decisions recorded; the source design's merge-sort bootstrap replaced with Bradley-Terry after the sort was found structurally unable to deliver the cycle capture that design calls its primary output.

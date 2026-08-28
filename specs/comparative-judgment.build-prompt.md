# Build prompt — comparative-judgment, **phase 1**

> Hand this file to a building agent (a fresh Claude Code session, Cursor, Aider, …). It targets
> **phase 1 only**. The full target is `specs/comparative-judgment.md`; the reasoning behind every
> settled fork, including one that overturns the source design, is in
> `specs/comparative-judgment.decisions.md` (D1–D15).

## Recommended build-time session settings
- **Model:** Claude Opus 5. **Effort:** `xhigh` (Extra).
- Most of this phase is well-specified and mechanical. The **Bradley-Terry fit is the exception**:
  a convergence path that varies with input order produces plausible numbers that are quietly
  wrong. Two acceptance criteria exist specifically to catch that — byte-identical refit, and
  insertion-order invariance. Run them early rather than at the end.
- **Standard errors are NOT in this phase (D14).** The Fisher-information derivation and its
  pseudo-inverse were the largest and subtlest piece of work here, and nothing in phase 1 consumes
  them: band placement compares scale values against cut thresholds, and the stopping rule counts
  appearances. They move to phase 2 with the diagnostics that need them. Do not build them.

## Read first
1. `specs/comparative-judgment.md` — the whole target.
2. `specs/comparative-judgment.decisions.md` — D1–D15. **Read D2 carefully**: it explains why the
   source design's merge sort was rejected, and repeating that reasoning back is a good check that
   you have understood the data model.
3. The "Not checked" section of that record — the things this specification did *not* verify.

## Work in this order
1. Restate the outcome in one sentence.
2. Review the spec's **assumptions** block. Flag anything that looks wrong and confirm with the
   human BEFORE building.
3. List remaining ambiguities.
4. **PLAN GATE — enter plan mode, present an implementation plan for phase 1, get human approval
   BEFORE writing code.**
5. Build **only** phase 1. Treat higher-phase items as documented-but-not-yet: do not build them,
   and do not make architectural choices that block them.
6. Verify each phase-1 acceptance criterion **by running it**. Do not mark the phase complete until
   all pass.

---

## HARD CONSTRAINTS

**No language model, anywhere. Ever.** This is not a phase-one simplification — it is a permanent
architectural property. The tool exists to make *human* severity judgment reliable, and its output
becomes the ground truth against which an LLM judge is later measured. A model inside it would make
the measuring instrument depend on the thing being measured. An acceptance criterion scans the
lockfile for model dependencies.

**No network calls, anywhere.** An acceptance criterion fails the run if a socket is opened.

**The core must be UI-agnostic from the first commit.** A web adapter is expected later (D3), and
these three mistakes are what would make this phase's work throwaway:
- session and undo state living in TUI widget state rather than in the core — **the most likely
  error and the most expensive**;
- pair selection or persistence called from UI event handlers;
- the core returning preformatted terminal strings. It must return **structured** presentation data
  — observation, evidence, consequence as fields — which the TUI renders as panes and a web adapter
  later renders as HTML. Formatting in the core is the subtlest way to bake in a terminal assumption.

**The tool never mutates the caller's findings file.** It reads YAML findings and writes its own
severity file keyed by finding id (D5).

**Never do unattended:** deleting a comparison record (retraction is an appended record, never a
deletion); emitting a band for a finding with no comparison behind it; writing outside paths the
caller supplied.

---

## Phase 1 scope — required floor

1. **Core data model + append-only comparison log.** Findings carry a stable id *and a content hash*
   — revised text becomes a new item, and comparisons stay bound to the version actually judged.
   Comparisons carry both item ids, the outcome, rater id, session id and a UTC timestamp. The rater
   id is present from the first commit even though there is one rater (D4): retrofitting it later
   leaves every existing comparison unattributable.
2. **Regularised Bradley-Terry fit** by maximum likelihood (the standard MM iteration), producing
   scale values only — **no standard errors in this phase (D14)**. **Determinism must be engineered,
   not hoped for**: fixed convergence tolerance, fixed iteration cap, deterministic item ordering,
   deterministic tie-breaking. Non-convergence within the cap reports failure and emits nothing.
   **Regularisation is required, not optional (D13):** apply a symmetric prior of λ = 0.5
   pseudo-wins and λ = 0.5 pseudo-losses per item against a virtual opponent at the scale origin.
   Without it the estimate diverges for any item that wins or loses *all* its comparisons —
   guaranteed at both ends of a severity scale, and the failure is silent: the iteration drifts to
   its cap and returns large arbitrary numbers that look like data.
3. **Session API** — `next_pair`, `record`, `undo`, `progress`, `estimates`. The only surface any
   front end may touch.
4. **Band cuts, calibration and placement.** Exactly three absolute judgments (Critical/High,
   High/Medium, Medium/Low), each made with findings visible on both sides. At least the top cut is
   tied to a written consequence definition, because a pairwise scale is *relative with no origin* —
   the ordering can be internally perfect while the whole set sits a band too high.
   **A cut is stored as the ordered pair of findings either side of it (D12)**, never as a
   scale-value threshold, and its threshold is recomputed as their midpoint at each fit. A stored
   number would mean something only relative to the fit that produced it. If a refit inverts an
   anchor pair, report that cut by name rather than re-ordering it silently.
5. **YAML findings reader and severity writer.** Each findings entry carries `id`, `observation`,
   `evidence` (a **list** — fragments stay separate), `consequence`, `detectable_by` and `tier`.
   **Entries with `tier: question` are excluded** from the batch, the fit and the anchor set, with
   the excluded count reported (D10): they are non-defects with no consequence to compare, and a
   rated question row would become an anchor that silently distorts every later placement. The
   severity file names the run id, anchor-set version and the hash of the log it was computed from.
6. **Repo hygiene floor.** `.gitignore` covering the conventional store location named in the
   documentation (`.cj-store/`), plus `LICENSE`; and `uv.lock` with exact pinned versions once
   dependencies are declared. There is no implicit store path — invoking without one fails by name
   (D9) — but the documented location is ignored so following the examples cannot produce a file
   that quietly wants committing. The store holds findings text, which for other users may be real
   production defect descriptions.

## Phase 1 scope — chosen optional items

7. **Comparison TUI** — two findings side by side, single-keypress choice, **undo**, mark-as-tie and
   per-item appearance progress. The session timer and the comparisons-remaining estimate move to
   phase 2 (D14). Undo is not a nicety: in a 250-comparison session a misfired key otherwise becomes
   a silently wrong datum in the log that everything downstream derives from.
8. **Resumable sessions.** The bootstrap is ~250 comparisons and is expected to span sittings.
   Largely a matter of reading back the append-only log.
9. **Simple heuristic pairing** — pair items whose current estimates are close. This is what makes
   the ~10-appearances estimate plausible rather than optimistic. Adaptive information-maximising
   selection is phase 3; do not build it now.
   **The cold-start phase ends when every admitted item reaches a configured appearance target,
   default 10 (D11)**, with per-item progress shown; the rater may stop earlier or continue. That
   target IS the spec's own estimate, so the first bootstrap validates or refutes it.

**Deferred by choice:** the connectivity check moves to phase 2 — phase 1 already refuses
cross-component comparisons, and a single batch is connected by construction, so the diagnostic
explaining why would have nothing to report.

---

## Determinism and type discipline
Everything in this tool is deterministic plain code. Apply `mypy --strict` with no ignores, frozen
dataclasses for every record type, `typing.Final` for constants, tuples for fixed collections.

Stack: Python 3.12+, `uv` with a committed lockfile pinning **exact** versions (not floors),
`pytest`, `mypy --strict`, `ruff`, `textual`, `PyYAML`, `numpy`.

---

## Phase 1 acceptance criteria — verify by RUNNING each

- [ ] A cold-start batch of 50 findings reaches a total order; three cuts are set with exactly three
      absolute judgments; every finding receives a band.
- [ ] Placing a 51st finding against established cuts costs no more than 4 comparisons, asserted by
      counting records.
- [ ] Two fits over an unchanged log produce byte-identical scale values and bands.
- [ ] A log built in a different insertion order, containing the same judgments, produces the same
      bands.
- [ ] A session interrupted mid-batch resumes at the same position with the same spent count.
- [ ] A session killed immediately after a keypress retains that comparison on restart — proving it
      was persisted before the next pair was presented.
- [ ] A finding whose text is edited becomes a new item; comparisons against the previous text stay
      attached to it.
- [ ] A retracted comparison is absent from the fit while its record remains in the log.
- [ ] A tie is recorded, excluded from the fit, and counted in the tie rate.
- [ ] A deliberately intransitive triad (A>B, B>C, C>A) is accepted without error rather than
      rejected, and leaves the items it involves indistinguishable on the scale. (The misfit half
      of this belongs to phase 2, with the standard errors D14 moved there.)
- [ ] An item winning every one of its comparisons receives a finite scale value, and the fit
      converges within its cap rather than reaching it.
- [ ] A cut round-trips as an ordered pair of findings, with its threshold recomputed from current
      scale values rather than stored as a number.
- [ ] A refit that inverts a cut's anchor pair reports that cut by name.
- [ ] A batch reports per-item appearance progress and completes only when every admitted item
      reaches the configured target.
- [ ] Two batches with no bridging comparisons are reported as separate components; no
      cross-component band comparison is emitted.
- [ ] A fit forced past its iteration cap reports non-convergence and emits no values.
- [ ] A finding missing `id`, observation or consequence text is refused by name.
- [ ] A findings file mixing `defect` and `question` tiers admits only defects, excludes questions
      from the fit and the anchor set, and reports the excluded count.
- [ ] A findings entry whose `evidence` holds three fragments is read with all three still distinct.
- [ ] A finding with no comparison against it receives no band and is reported as unplaced.
- [ ] A store with an unrecognised schema version refuses to write and names the mismatch.
- [ ] Invoking with no store path fails by name rather than writing inside the repository.
- [ ] A recorded comparison carries all six fields; the count missing any is zero.
- [ ] A running session displays comparisons spent and per-item appearance progress.
- [ ] A completed batch reports mean comparisons per finding placed and mean appearances per item,
      compared against the spec's ~3 and ~10 estimates (D8).
- [ ] The findings file is unchanged after a full scoring run, asserted by content hash.
- [ ] `mypy --strict`, `ruff check` and `ruff format --check` all pass.
- [ ] The core package imports no UI library, asserted by a static import scan.
- [ ] The TUI reaches the core only through the session interface, asserted by scanning for imports
      of core internals from presentation modules.
- [ ] The session interface returns structured objects, asserted by rendering the same pair through
      two different formatters.
- [ ] Record types are frozen dataclasses and constants are `typing.Final`, asserted by a mutation
      attempt that expects failure.
- [ ] No module opens a socket, asserted by a test that fails the run if one is opened.
- [ ] The lockfile contains no language-model dependency.
- [ ] `.gitignore` covers the default store location, present in the first commit.
- [ ] `uv.lock` pins exact resolved versions; no dependency is a floor only.
- [ ] The README states that findings text may be sensitive and that the default store is gitignored.
- [ ] A fit over 1,000 findings and 10,000 comparisons completes in under five seconds.

---

## Reporting back
- Append any fork you resolve to `specs/comparative-judgment.decisions.md` from **D16**, in the same
  shape (fork, options, decision, why, consequences) — and each entry from D16 onward must end with a
  `**Rule** — …` line naming what enforces it: a test, a scan, or explicitly *judgment, not
  checkable*.
- Record architectural calls in the spec's **decisions made** block.
- If the spec changes, add a changelog line and bump the version.
- Do not add packages outside "constraints" without flagging first.

**Regeneration test:** could an agent rebuild phase 1 from the spec alone and produce behaviourally
identical output? If not, you have found what the spec is missing — fix it *there*, not only in the
code.

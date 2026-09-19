# Build prompt — comparative-judgment, **phase 2**

> Emitted on 2026-09-18 from specification **0.11.0 @ D36**, by the `/specify` skill's
> advance-phase step: the phase's `[P2]` items, sliced from the spec, not re-interviewed. The
> phase-1 prompt, `specs/comparative-judgment.build-prompt.md`, is a frozen record of what phase 1
> was told and is not the brief for this one. Where this file and the spec disagree, **the spec
> wins**: fix this file, or raise it.

## Recommended build-time session settings
- **Model:** Claude Opus 5. **Effort:** `xhigh` (Extra) — confirmed by the owner at the
  build-readiness check before this prompt was handed over.
- The load is statistical rather than mechanical. Standard errors from the Fisher information of a
  **regularized** likelihood, a misfit statistic that has to behave on a sparse, placement-shaped
  comparison graph, and an anchor import that refits a scale other decisions depend on are each
  places where plausible numbers can be quietly wrong. Build the statistics first and test them on
  constructed inputs whose answers are known — the intransitive triad, an item winning everything,
  two disconnected batches — before any front end shows them.
- Fresh session recommended. This prompt is self-contained; it does not assume the context of the
  session that wrote it.

## Read first
1. `specs/comparative-judgment.md` — the whole target, not only the `[P2]` lines.
2. `specs/comparative-judgment.decisions.md`, D1–D36. Read these closely:
   - **D2** — Bradley-Terry was chosen *because* it reports inconsistency as graded misfit. Phase 2
     is where that promise is delivered; a misfit statistic that cannot see the triad breaks it.
   - **D12, D13** — cuts are anchor pairs; the fit carries λ = 0.5 pseudo-wins and pseudo-losses
     against a virtual opponent at the origin. That prior is part of the likelihood whose
     information you are inverting.
   - **D14** — what moved into this phase, and why.
   - **D21, D23, D24, D32** — connectivity is enforced; the run id hashes `anchor_set_version`; the
     session seam covers the whole tool; tests may reach past it and front ends may not.
   - **D33, D34** — the severity file's fields are enumerated in **both** specifications and
     compared by the consuming harness's scanner.
   - **D35, D36** — every writer writes LF; a band, once assigned, is frozen, and anything that
     refits the scale can only propose a change.
3. The decision record's *Not checked* section — in particular interactive latency, which it calls
   "a phase-2 requirement waiting to be written", and `anchor_set_version`, which no caller can set
   until this phase's import/export exists.

## Work in this order
1. Restate the phase's outcome in one sentence.
2. Review the spec's **assumptions** block. Flag anything that looks wrong and confirm with the
   human BEFORE building.
3. Work through **Decisions owed at the plan gate** below. For each, present the options, a
   recommendation and why — **do not choose silently**. Each answer becomes a decision in the
   record, from D37, in the record's usual shape, ending with a `**Rule** — …` line.
4. **PLAN GATE — enter plan mode, present an implementation plan for phase 2 together with those
   decisions, and get human approval BEFORE writing code.**
5. Build **only** phase 2. Phase-3 and phase-4 items are documented-but-not-yet: do not build them,
   and do not make choices that block them — adaptive selection (phase 3) will consume the standard
   errors you build, and a web adapter (phase 4) will consume the diagnostics through the session.
6. Verify each phase-2 acceptance criterion **by running it**, and the whole existing suite with
   it. Do not mark the phase complete until all pass.

---

## HARD CONSTRAINTS

Everything phase 1 was held to still holds, and the record has added to it since:

- **No language model, anywhere, ever**, and **no network call** of any kind. Both are asserted by
  existing tests.
- **The session is the only surface a front end touches (D24).** Every new operation — diagnostics,
  import, export of an anchor set, the timer and the estimate — is reached through `Session` and
  returns **structured** data. The CLI and the TUI format it. A scan enforces imports *and*
  attribute reach-throughs (D32).
- **Validate, then write (D26).** A refused operation leaves the store byte-identical, and every
  parse boundary raises a named refusal — which matters most for an import reading a file somebody
  else produced.
- **Every file this tool writes asks for LF (D35)**, including any anchor-set file and any report
  written to disk.
- **Frozen bands (D36).** Nothing in this phase assigns a band. An import that brings comparisons
  refits the scale, and whatever it moves is a *proposal* the rater accepts through `cj assign`.
- **The severity file's field set does not change in this phase** unless both specifications are
  amended together: its top-level and row fields are enumerated here and in the consuming harness,
  and that harness's scanner compares them on every push that touches `specs/`. Diagnostics get
  their own output. If you believe a severity field is needed, flag it at the plan gate.
- **Never do unattended:** mutating the caller's findings file; deleting a comparison record;
  emitting a band for a finding with no comparison behind it; changing an assigned band without a
  rater's accepted assignment; writing outside paths the caller supplied.
- **No new packages without flagging first.** `numpy` is already declared; anything else — `scipy`
  included — is asked for, not added.

---

## Phase 2 scope — from the spec's `[P2]` tags

1. **Per-item standard errors** from the Fisher information, reproducible under the same
   conditions as the scale values: two fits over an unchanged log give byte-identical standard
   errors.
2. **Diagnostics report** — per-item standard errors, misfit statistics and the tie rate, and the
   regions of the scale with the highest misfit, named as regions and not only as per-item values.
   A deliberately intransitive triad must raise the misfit of the items it involves.
3. **Anchor-set export and import.** An exported anchor set includes the findings, their comparisons
   and the band-cut definitions needed to reuse it elsewhere. Importing it reports how many
   comparisons bridge the imported set to the existing one, and the connectivity report names the
   components that are under-bridged.
4. **Session timer and comparisons-remaining estimate**, displayed while a session runs, beside the
   comparisons already spent.

**Done when:** an intransitive triad is visible as elevated misfit, and an anchor set moves between
stores with its bridging count reported — the spec's own condition for this phase.

## Decisions owed at the plan gate

The spec names these outcomes and does not settle how to reach them. Each is the owner's call;
bring options, not an answer already built.

1. **Which misfit statistic.** For example infit and outfit mean-squares of standardized residuals
   over decided comparisons. The fit excludes ties, so say whether and how ties enter misfit.
2. **What a "soft region" is.** How the scale is partitioned — by band, by a window of scale values,
   by neighbors — and what "highest misfit" ranks within it.
3. **How the standard errors treat the prior.** The virtual opponent adds information, so a standard
   error from the regularized information and one from the data alone differ most at the extremes,
   which are the Critical and Low findings. Name which is reported, and why.
4. **What the remaining-comparisons estimate counts** in each mode — appearances short of the target
   while bootstrapping, newcomers short of their placement quota once cuts exist — and **what the
   session timer measures**, given that no session keeps a start or end record: the next pair is
   derived from the log.
5. **The anchor-set file and how an import merges.** Its format and contents (findings with content
   hashes; comparisons with or without their retractions; the cuts with their calibration notes;
   assignments or not), what an identifier collision or a content-hash conflict does — the D18 and
   D22 refusals are the precedent — and how `anchor_set_version` becomes settable, since the run id
   hashes it.
6. **An import and frozen bands.** Confirm that an import leaves assignment to the rater: imported
   findings arrive as first-time bands, and existing assigned bands the refit moves arrive as
   proposals.
7. **What "under-bridged" means.** A count below which a component is named is a threshold, and D34
   declined to invent one for a similar question. Reporting every component's bridging count and
   refusing nothing is one option.
8. **Where diagnostics surface.** A subcommand, a TUI pane, both — and in what file, if any, a
   diagnostics report is written.
9. **Whether the latency requirement is written now.** *Not checked* calls it a phase-2 requirement
   waiting to be written, and this phase adds work to every keypress: the timer, the estimate, and
   D36's proposal read in `progress()`. The consuming harness raises the stakes: its phase 7 (its
   D191) calls for a fresh, larger held-out set, whose findings would be banded here in a separate
   store — possibly past the n = 100 that `MAX_ITER` was measured to, and further along a
   per-keypress cost that grows with n.

---

## Determinism and type discipline
Everything is deterministic plain code: `mypy --strict` with no ignores, frozen dataclasses for
every record type, `typing.Final` for constants, tuples for fixed collections. Standard errors are
reproducible byte for byte over an unchanged log, which means a fixed item ordering into every
matrix and no dependence on dictionary or set order. A linear-algebra call can differ between BLAS
builds, so the byte-identity asserted is across two runs on one machine; say so where the
guarantee is stated rather than implying more.

Stack: Python 3.12+, `uv` with the committed lockfile pinning **exact** versions, `pytest`,
`mypy --strict`, `ruff`, `textual`, `PyYAML`, `numpy`.

---

## Phase 2 acceptance criteria — verify by RUNNING each

- [ ] A diagnostics report names per-item standard errors, misfit statistics and the tie rate.
- [ ] A diagnostics run names the specific scale regions with the highest misfit, not merely
      per-item values.
- [ ] A deliberately intransitive triad (A>B, B>C, C>A) raises the misfit statistic for the items it
      involves.
- [ ] Standard errors are byte-identical across two fits over an unchanged log.
- [ ] An exported anchor set imports into an empty store and reports its bridging-comparison count.
- [ ] A running session displays an estimate of comparisons remaining and elapsed session time.
- [ ] Every phase-1 criterion still passes: the full suite, `mypy --strict`, `ruff check` and
      `ruff format --check`.

The spec leaves these criteria **unticked** on purpose (D30): what a criterion is worth is the test
its decision's `**Rule**` line names.

---

## Reporting back
- Append each fork you resolve to `specs/comparative-judgment.decisions.md` from **D37**, in the
  record's shape — fork, options, decision, why, consequences — ending with a `**Rule** — …` line
  that names the test, the scan, or explicitly *judgment, not checkable*. Update *Document status*
  and the high-water line; a test reads both.
- Record architectural calls in the spec's **decisions made** block.
- If the spec changes, add a changelog line and bump the version. A change under `specs/` asks the
  consuming harness to run its interface scanner when it is pushed (D31); let that run.
- Document every new command in the README.
- Do not add packages outside "constraints" without flagging first.

**Regeneration test:** could an agent rebuild phase 2 from the spec alone and produce behaviorally
identical output? If not, you have found what the spec is missing — fix it *there*, not only in the
code.

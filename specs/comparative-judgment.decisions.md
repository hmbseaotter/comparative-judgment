# comparative-judgment — Decision Record

- **Project:** comparative-judgment — severity scoring by pairwise comparison
- **Identity:** A standalone tool that lets a rater assign defensible severity to findings by answering only "which of these two is worse?", never by picking a number on a scale.
- **Spec:** `specs/comparative-judgment.md`
- **Status:** D1–D9 recorded. D1–D8 from the /specify session of 2026-08-28; D9 settled at emit, resolving a contradiction the linter surfaced.
- **Legend:** ✅ decided · 🔶 open / revisit · ⏭️ deferred to a later phase

<!-- rules-required-from: D9 -->

> **Rules are required from D9 onward.** Every decision recorded from the build phase on ends with a
> `**Rule** — …` line naming what enforces it: a test, a scan, or explicitly *judgment, not
> checkable*. D1–D8 predate this and are exempt.

> **Numbering is allocate-once.** Never renumber and never reuse a retired number. Supersede rather
> than rewrite.

> **Source design.** This tool was proposed as "D5" in a prior handover reference document, which
> supplied the method, the four design constraints and the cold-start bootstrap. That proposal is
> input, not gospel: D2 below overturns its central algorithmic choice for a reason the proposal did
> not anticipate.

---

## D1 — Visibility and data handling

**Fork:** The tool stores findings text. For other users that could be real production defect descriptions rather than synthetic ones. Public or private, and where does the store live?

**Options considered**
- **(A) Public tool, data store gitignored by default.**
- **(B) Public tool, data store committed by default** — anchor set travels with the repo.
- **(C) Private, personal use only.**
- **(D) Public, store path always supplied by the caller, no default inside the repo.**

**Decision ✅** — **(A)**, with (D)'s discipline partly adopted: the store path is always supplied by the caller, and the conventional documented location is gitignored.

> **Refined by D9.** This entry originally read "there is a default store location, it is gitignored" while a requirement elsewhere said invoking with no path must fail by name — a contradiction. D9 resolves it: no implicit fallback exists, but the location the documentation uses is gitignored.

**Why** — A general-purpose severity tool will eventually be pointed at real production findings by someone, and a default that quietly wants committing is a trap laid for that person. (B) suits this project specifically — shared anchors travelling with the repo is exactly what the source design's bootstrap argues for — but it optimises for the author at a stranger's expense. (C) was rejected because the source design explicitly frames this as "a clean standalone open-source artifact in its own right", and the decision to separate it from the harness was made partly so it could stand alone.

**Consequences / caveats** — The author's own anchor set for the harness corpus can still be committed deliberately, since those findings are synthetic. The README must state that findings text may be sensitive.

---

## D2 — Bradley-Terry rather than a comparison sort

**Fork:** The source design specifies a merge-style pairwise sort for the cold-start bootstrap, and separately says *"treat intransitivity as the primary output, not an error"* and *"capture cycles from the very first session."* Can both hold?

**Options considered**
- **(A) Comparison log as source of truth; merge sort plus a targeted redundancy probe now; Bradley-Terry fittable later over the same data.**
- **(B) Bradley-Terry core now with simple pairing; adaptive selection deferred.**
- **(C) Full adaptive comparative judgment with Bradley-Terry from the start.**
- **(D) Merge sort exactly as specified, cycle capture explicitly downgraded to a nice-to-have.**

**Decision ✅** — **(B).**

**Why** — The sort cannot deliver what the design calls its most valuable output, and the reason is structural rather than incidental: **a comparison sort's efficiency *is* its transitivity assumption.** For 50 findings it makes ~237 comparisons out of 1,225 possible pairs — about 19% — and it skips precisely the third leg of any potential cycle, because that is the comparison transitivity lets it avoid. Worse, given an intransitive rater it still returns a confident total order, one that would differ under a different comparison order, with nothing signalling that anything went wrong.

Bradley-Terry assumes no total order. It fits each item a scale value with a standard error from whatever pairwise outcomes exist, and inconsistency surfaces as poor fit, wide standard errors and per-item misfit — a graded map of where the scale is soft, which is strictly richer than a binary cycle flag. It is also what the source design's own instruction to "treat the literature as a starting point" actually points at; the sort was the deviation.

Human effort is comparable: at ~10 appearances per item, 50 findings costs ~250 comparisons — the same budget the sort was already granted. The decision therefore costs build effort, not rater time.

(A) was the initial recommendation and was withdrawn on examination. Its claim that a later Bradley-Terry fit would need "nothing re-asked" was **wrong**: the format migrates cleanly but the comparison *distribution* does not. Band-targeted placement produces a sparse, structurally biased graph — items connected only to cut-adjacent anchors and never to each other — on which a fit yields huge standard errors and possibly non-identifiable estimates. Migration would need roughly 7 more comparisons per item: at 1,000 findings, ~7,000 additional human judgments, paid in the scarce resource. (C) is the right end state but front-loads adaptive selection, the part least felt at ~70 findings.

**Consequences / caveats** — Bradley-Terry's core is small (MM iteration plus standard errors from the Fisher information), so "power tool" overstates it; the expensive part is adaptive selection, which is separable and deferred. The fit is iterative, so reproducibility must be engineered deliberately — fixed tolerance, fixed iteration cap, deterministic ordering and tie-breaking. Scale values are *derived*, so a refit can move a band; bands are frozen at assignment and a refit produces a proposed revision for review rather than silently relabelling something the consuming harness already cites.

---

## D3 — Interface, and the UI-agnostic seam

**Fork:** What does the comparison loop look like? The tool's value is reducing cognitive load, so per-comparison friction cancels the method's benefit.

**Options considered**
- **(A) Keyboard-driven TUI, single process.**
- **(B) Plain CLI printing two findings and reading a keypress.**
- **(C) Local web app.**
- **(D) File-based batch — export pairs, fill in answers, re-import.**

**Decision ✅** — **(A) now, (C) expected later**, with the explicit constraint that phase-one work must not become throwaway when the web adapter arrives.

**Why** — A finding is 3–6 lines, so two fit a terminal comfortably; a TUI gives a stable layout, progress, and undo without a server, a port or a browser. (B) re-prints everything each round with no stable layout and no easy undo — over hundreds of comparisons that friction is exactly the fatigue the method exists to avoid. (D) is ergonomically worst for the task that dominates the effort and loses per-comparison timing, a useful fatigue signal.

**Consequences / caveats** — The core must be UI-agnostic from the first commit, which is a real design constraint rather than a preference. Three specific mistakes would create the throwaway work: session and undo state living in TUI widget state rather than the core (the most likely, and most expensive); pair selection or persistence called from UI event handlers; and the core returning preformatted terminal strings rather than structured presentation data. The last is the subtlest way to bake in a terminal assumption.

---

## D4 — Rater identity in the data model

**Fork:** Single rater or multiple? Every comparison record carries a rater id or it does not.

**Options considered**
- **(A) Record rater identity from day one; multi-rater analysis deferred.**
- **(B) Full multi-rater support now** — per-rater scales, inter-rater agreement, disagreement localisation.
- **(C) Single rater, no rater field.**

**Decision ✅** — **(A).**

**Why** — The field costs essentially nothing, and its absence is unfixable: retrofitting it later leaves every already-recorded comparison unattributable. (C) also forecloses a stated goal — the source methodology treats reviewer disagreement as a primary source of insight, having measured two independent reviewers disagreeing on roughly 21 ratings. (B) builds analysis machinery before a second rater exists.

**Consequences / caveats** — Same shape as D2: pay the cheap structural cost now, defer the machinery. Multi-rater analysis lands in phase 3, over data that already supports it.

---

## D5 — Findings interchange format and severity ownership

**Fork:** How do findings enter the tool and severities leave it? Neither this spec nor the consuming harness's spec named a format.

**Options considered**
- **(A) YAML findings file; tool writes a separate severity file keyed by finding id.**
- **(B) YAML findings file; tool writes severity back into it.**
- **(C) Markdown table as source of truth.**
- **(D) JSON or JSONL.**

**Decision ✅** — **(A).**

**Why** — The constraint that decides it is evidence fields: they hold verbatim multi-line quotes, and the row schema requires fragments from different points in a call to be visibly separate lines. A Markdown table cell physically cannot hold a newline or an unescaped pipe, and CSV quoting is fragile at exactly that job — so (C) would force flattening evidence, losing the distinction the schema exists to preserve. (D) is machine-clean but poor for a human authoring multi-paragraph prose with no comments. YAML block scalars carry it naturally, stay diffable, and match the consuming harness's existing pattern of data-as-YAML with readable output generated from it.

Severity ownership went to a separate file because the tool must never mutate a document it does not own: that keeps provenance explicit (which run, which anchor set, which log), and it lets the tool work on corpora where it has no write access.

**Consequences / caveats** — **This settles a gap in the consuming harness's specification, which names no format for its findings document; that spec needs amending to match.** A Markdown table view can be generated from the YAML for readability without becoming the source of truth.

---

## D6 — v1 exclusions

**Fork:** Which of the tool's stated capabilities are out of the v1 target?

**Options considered** — four candidates: judge-versus-human agreement measurement, rater drift detection, cross-corpus anchor import/export, and per-item misfit diagnostics.

**Decision ✅** — **Exclude judge-versus-human agreement measurement and rater drift detection.** Anchor import/export and misfit diagnostics stay in the target at phase 2.

**Why** — Both exclusions are excluded for the same reason: **they cannot be validated now.** Agreement measurement consumes LLM judge verdicts that will not exist until the consuming harness reaches its own phase 4, so building it means designing against an imagined input and testing against invented fixtures. Drift detection needs longitudinal data that will not exist for months — and you cannot test drift detection without drift. Misfit diagnostics were kept because they are the successor to the source design's cycle capture, which that design calls the thing turning the tool from a scoring aid into a rubric-improvement engine; Bradley-Terry gives them nearly free once the fit exists, so excluding them saves little and discards the tool's most distinctive output.

**Consequences / caveats** — The tool ships doing two of its three stated jobs. That should be stated plainly in the README rather than left for a reader to discover.

---

## D7 — Tie handling

**Fork:** A rater will sometimes judge two findings genuinely equal. Force a winner, record a tie, or model ties properly?

**Options considered**
- **(A) Record ties, exclude them from the fit, report the tie rate.**
- **(B) Force a choice** — no tie option.
- **(C) Fit ties properly via Davidson's extension to Bradley-Terry.**

**Decision ✅** — **(A)**, with (C) deferred.

**Why** — (B) manufactures a false signal: a forced coin-flip on a genuinely equal pair enters the log indistinguishable from a real judgment, and corrupts the scale. (C) is correct but adds a parameter and complexity to the fit before there is any evidence ties are frequent enough to bias it. (A) keeps the information — a tie is recorded and visible — while leaving the fit simple, and the **tie rate becomes a scale-softness measure in its own right**, in the same family as misfit.

**Consequences / caveats** — Rests on ties being rare; recorded as an assumption. If the tie rate turns out high, Davidson's extension is needed sooner than phase 3, and the recorded ties are already there to fit.

---

## D8 — Measuring the cost model rather than assuming it

**Fork:** The two estimates underpinning every cost claim — ~10 appearances per item for adequate reliability, ~3 comparisons to place a finding against three cuts — are drawn from the literature and from arithmetic, not from measurement. Leave them as assumptions, or make the tool check them?

**Options considered**
- **(A) Add a requirement that the tool reports actual comparisons per finding placed and actual appearances per item.**
- **(B) Leave both as recorded assumptions with their risk noted.**

**Decision ✅** — **(A).**

**Why** — The tool already logs every comparison, so the measurement is nearly free, and it converts the two load-bearing estimates from claims into figures the first bootstrap validates or refutes. It is the same discipline the consuming project applies to its own claims, applied here rather than exempted. Leaving them unmeasured would mean the entire scaling argument — ~3,000 comparisons for 1,000 findings rather than ~9,000 — rests permanently on arithmetic nobody checked.

**Consequences / caveats** — If measured figures diverge from the estimates, the spec's cost projections need revising, not the tool. Assumptions 3 and 4 are annotated as mitigated rather than removed, since measurement confirms them only after the first real batch.

---

## D9 — Store path: no implicit default, conventional location gitignored

**Fork:** The spec required both that invoking without a store path *fails by name rather than defaulting to a path inside the repository*, and that `.gitignore` covers *"the default store location"*. Those contradict: if there is no default, there is nothing to ignore.

**Options considered**
- **(A) No implicit fallback; a conventional documented location, gitignored.**
- **(B) A real implicit default inside the repo, gitignored** — simplest to use, one less argument on every invocation.
- **(C) No default and no gitignore entry** — nothing to ignore, so drop the requirement.

**Decision ✅** — **(A).** The tool never writes to a path the caller did not name. Separately, the location the documentation and examples use is gitignored, so a reader following the docs cannot produce a file that quietly wants committing.

**Why** — (B) is friendlier but reintroduces exactly the trap D1 rejected: a tool that writes findings text somewhere by default will eventually do so on a machine where those findings are real, and the person will not have chosen the location. (C) is internally consistent but leaves the documented happy path unprotected — the examples have to name *some* directory, and that is precisely the one a new user will create. (A) keeps the strict no-implicit-write property while still protecting the path people will actually use.

**Consequences / caveats** — The documentation and the `.gitignore` must name the same location, or the protection silently covers nothing. Found by the spec linter during emit, as a stamp-drift error rather than by review — the contradiction itself was mine, introduced while drafting two requirements minutes apart.

**Rule** — Two acceptance criteria, both enforced by test: invoking with no store path fails by name rather than writing inside the repository; and `.gitignore` covers the conventional store location named in the documentation. A third check belongs with them once a README exists: the path in the docs and the path in `.gitignore` must match, which is a scan, not judgment.

---

## Not checked — as of 0.1.0 @ D9

- **Bradley-Terry convergence behaviour was reasoned about, not tested.** The MM iteration is standard and convergent for connected graphs, but the interaction between the deterministic-ordering requirement and floating-point summation order has not been examined. The byte-identical-refit criterion is what will surface it.
- **`textual` was assumed suitable and not evaluated** against the specific need for stable side-by-side panes with single-keypress capture and undo.
- **The ~5 second performance target for 1,000 findings / 10,000 comparisons is an estimate**, not a benchmark; no fit has been run.
- **The consuming harness's findings document format was decided here (D5) but that spec has not yet been amended.** Until it is, two specs disagree — this one names YAML, the other names no format at all.
- **No literature review was performed.** The method, the ~10-appearances figure and the standard-error approach are taken from general knowledge of comparative judgment practice, not from cited sources. The source design's instruction was to treat the literature as a starting point; that has not been discharged.
- **Whether bands should be recomputed or frozen after a refit was decided in principle (freeze, propose revisions) but not specified in requirements** — no requirement or acceptance criterion covers the revision-proposal path.

---

## Document status

Decisions **D1–D9** recorded. The most consequential is **D2**, which overturns the source design's central algorithmic choice; **D5** additionally settles a gap in a second project's specification, which must be amended to match.

Spec: `specs/comparative-judgment.md`. Build prompt: `specs/comparative-judgment.build-prompt.md` (phase 1).

Any new fork encountered during the build is appended here in the same shape, and from **D9** onward each entry ends with a `**Rule**` line naming what enforces it. Numbering continues from **D10**.

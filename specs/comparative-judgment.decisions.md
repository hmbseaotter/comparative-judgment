# comparative-judgment — Decision Record

- **Project:** comparative-judgment — severity scoring by pairwise comparison
- **Identity:** A standalone tool that lets a rater assign defensible severity to findings by answering only "which of these two is worse?", never by picking a number on a scale.
- **Spec:** `specs/comparative-judgment.md`
- **Status:** see *Document status* at the end of this file for the current high-water mark, which is the one place it is maintained and the one place a test reads. This line carried its own copy until an audit filed it as C-9: a number written in two places is a number that goes stale in one of them, and this is the copy a reader meets first. The provenance is what the line is actually for. D1–D8 from the /specify session of 2026-08-28; D9 settled at emit, resolving a contradiction the linter surfaced; D10 at the cross-repository interface pass; D11–D13 at the phase-1 plan gate; D14–D15 at the sweep that followed; D16 during the build; D17–D18 at the post-build sweep; **D19–D26 after an independent audit of 0.5.0** — a session that had written none of the code, read the spec, the record and the source, then ran the tool against constructed inputs and reported forty-one findings; **D27 from the audit that followed**, which read all three repositories.
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

**Why** — A general-purpose severity tool will eventually be pointed at real production findings by someone, and a default that quietly wants committing is a trap laid for that person. (B) suits this project specifically — shared anchors traveling with the repo is exactly what the source design's bootstrap argues for — but it optimizes for the author at a stranger's expense. (C) was rejected because the source design explicitly frames this as "a clean standalone open-source artifact in its own right", and the decision to separate it from the harness was made partly so it could stand alone.

**Consequences / caveats** — The author's own anchor set for the harness's **design set** can still be committed deliberately, since those findings are synthetic *and* public. The README must state that findings text may be sensitive.

> **Narrowed after the harness's D61.** This line originally licensed committing the anchor set *"since those findings are synthetic"*. The harness's **held-out** findings are synthetic too, so as written it licensed them equally — and a findings document's `observation` and `consequence` for a held-out call are that call's defect label written out in prose. This repository stands alone by D1's own reasoning, which means it carries none of the harness's held-out machinery: no absence scan, no declaration files, no CI assertion about labels. The property that licenses committing is *design-set*, not *synthetic*. Nothing is retracted by this note — no store exists in this tree and no anchor or comparison data is tracked — but the sentence would have been read at phase 5, when the held-out findings are scored with this tool, which is exactly when the wrong word would have been load-bearing.

---

## D2 — Bradley-Terry rather than a comparison sort

**Fork:** The source design specifies a merge-style pairwise sort for the cold-start bootstrap, and separately says *"treat intransitivity as the primary output, not an error"* and *"capture cycles from the very first session."* Can both hold?

**Options considered**
- **(A) Comparison log as source of truth; merge sort plus a targeted redundancy probe now; Bradley-Terry fittable later over the same data.**
- **(B) Bradley-Terry core now with simple pairing; adaptive selection deferred.**
- **(C) Full adaptive comparative judgment with Bradley-Terry from the start.**
- **(D) Merge sort exactly as specified, cycle capture explicitly downgraded to a nice-to-have.**

**Decision ✅** — **(B).**

**Why** — The sort cannot deliver what the design calls its most valuable output, and the reason is structural rather than incidental: **a comparison sort's efficiency *is* its transitivity assumption.** For 50 findings it makes ~237 comparisons out of 1,225 possible pairs — about 19% — and it skips precisely the third leg of any potential cycle, because that is the comparison transitivity lets it avoid. Worse, given an intransitive rater it still returns a confident total order, one that would differ under a different comparison order, with nothing signaling that anything went wrong.

Bradley-Terry assumes no total order. It fits each item a scale value with a standard error from whatever pairwise outcomes exist, and inconsistency surfaces as poor fit, wide standard errors and per-item misfit — a graded map of where the scale is soft, which is strictly richer than a binary cycle flag. It is also what the source design's own instruction to "treat the literature as a starting point" actually points at; the sort was the deviation.

Human effort is comparable: at ~10 appearances per item, 50 findings costs ~250 comparisons — the same budget the sort was already granted. The decision therefore costs build effort, not rater time.

(A) was the initial recommendation and was withdrawn on examination. Its claim that a later Bradley-Terry fit would need "nothing re-asked" was **wrong**: the format migrates cleanly but the comparison *distribution* does not. Band-targeted placement produces a sparse, structurally biased graph — items connected only to cut-adjacent anchors and never to each other — on which a fit yields huge standard errors and possibly non-identifiable estimates. Migration would need roughly 7 more comparisons per item: at 1,000 findings, ~7,000 additional human judgments, paid in the scarce resource. (C) is the right end state but front-loads adaptive selection, the part least felt at ~70 findings.

**Consequences / caveats** — Bradley-Terry's core is small (MM iteration plus standard errors from the Fisher information), so "power tool" overstates it; the expensive part is adaptive selection, which is separable and deferred. The fit is iterative, so reproducibility must be engineered deliberately — fixed tolerance, fixed iteration cap, deterministic ordering and tie-breaking. Scale values are *derived*, so a refit can move a band; bands are frozen at assignment and a refit produces a proposed revision for review rather than silently relabeling something the consuming harness already cites.

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
- **(B) Full multi-rater support now** — per-rater scales, inter-rater agreement, disagreement localization.
- **(C) Single rater, no rater field.**

**Decision ✅** — **(A).**

**Why** — The field costs essentially nothing, and its absence is unfixable: retrofitting it later leaves every already-recorded comparison unattributable. (C) also forecloses a stated goal — the source methodology treats reviewer disagreement as a primary source of insight rather than noise, because disagreement localizes exactly where a scale is underspecified. No disagreement rate is quoted here: a figure from one reviewer pair on one corpus in one session cannot be generalized, and the argument rests on what disagreement *tells you*, not on how often it happened to occur. (B) builds analysis machinery before a second rater exists.

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

**Consequences / caveats** — ~~This settles a gap in the consuming harness's specification, which names no format for its findings document; that spec needs amending to match.~~ **Discharged.** The harness settled the same format as its own D22 and pushed it; both specifications now name YAML. A Markdown table view can be generated from the YAML for readability without becoming the source of truth.

> **Stale until 2026-08-28.** The struck sentence is the **fourth** member of the class D15 was built to close — a claim about another repository that stopped being true when that repository moved. D15's own reasoning names three instances and cites this exact sentence as the third; the 0.4.0 repair fixed its twin in the assumptions block and missed this copy. It survived because the scanner is pointed at the two *specs* and never at the two decision records, and it was found by a later audit widening that universe by one file per side. The generalizable part is not the sentence: it is that **the detector's coverage was narrower than the rule it enforces**, which is the same shape D24 records elsewhere in this document. The scanner now takes a path list.

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

## D10 — Findings schema, and excluding the Question tier

**Fork:** This spec referenced only `id`, `observation` and `consequence`, never enumerating the findings schema, while the consuming harness's spec now specifies six keys. It also said nothing about `tier`, which distinguishes defects from non-defect open questions. Should the tool know the full schema, and what does it do with `question` rows?

**Options considered**
- **(A) Enumerate the schema; exclude `tier: question` from batches, the fit and the anchor set, and report the excluded count.**
- **(B) Enumerate the schema; rate `question` rows like any other** — the rater can judge them if they appear.
- **(C) Leave the schema unenumerated** — the tool reads the fields it needs and ignores the rest.

**Decision ✅** — **(A).**

**Why** — (C) is what caused the problem: an interface described on one side only drifts, and the harness spec had already fixed six keys this document did not mention. (B) is worse than it sounds. Question-tier rows are **non-defects by definition** — open questions for the owning team about behavior that may be correct by design — so they have no consequence to compare against. Rating them is meaningless on its own terms, and the damage compounds: a rated question row enters the **anchor set**, where it becomes a reference point that every later placement is measured against. A scale anchored partly on items with no consequence is quietly wrong everywhere, and nothing in the output would reveal it. Reporting the excluded count keeps the exclusion visible rather than silent, so a rater who expected 70 findings and sees 63 admitted knows why.

**Consequences / caveats** — The tool now depends on `tier` being present, so it is a required key rather than an optional one — which the harness spec's schema already makes it. Found by executing D9's own stated rule (that the two specs' descriptions of this interface must stay in agreement), not by review — the first time that rule paid for itself, one decision after being written.

**Rule** — Three acceptance criteria, enforced by test: a findings file mixing tiers admits only defects and reports the excluded count; an entry missing `id`, observation or consequence is refused by name; and three evidence fragments survive reading as three. The cross-repository schema agreement itself remains *judgment, not checkable* — no linter spans both repositories, and both changelogs now say so.

---

## D11 — What ends the cold-start comparison phase

**Fork:** The spec said how pairs are selected before cuts exist, and put the confidence-based stopping rule in phase 3 — so phase 1 had no stated condition for when the bootstrap is done.

**Options considered**
- **(A) Target appearances per item (default 10), rater may stop early or continue.**
- **(B) Standard-error threshold** — stop when every item is estimated precisely enough.
- **(C) Rater-driven only.**
- **(D) Fixed total comparison budget.**

**Decision ✅** — **(A).** Progress is shown per item against the target; the phase completes when every admitted item reaches it.

**Why** — (B) is phase 3's confidence rule arriving early, and phase 3 was deferred precisely because its benefit appears at 1,000 findings rather than 70; it also makes session length unpredictable, which matters when a rater is budgeting a sitting. (C) offers no guidance at the moment guidance is most useful, and a rater who stops early leaves the fit under-determined with nothing saying so. (D) spends comparisons evenly rather than where they are needed. (A) additionally makes the target *be* the spec's own ~10 estimate, so the first bootstrap validates or refutes assumption 3 rather than leaving it untested.

**Consequences / caveats** — The target is a default, not a floor: the rater overrides in both directions, and the measured appearances-per-item figure (D8) is what says whether 10 was right.

**Rule** — Acceptance criterion: a batch reports per-item appearance progress, and completes only when every admitted item reaches the configured target. Enforced by test.

---

## D12 — How a band cut is represented

**Fork:** A cut could be a scale-value threshold or a position between two findings. This decides whether bands stay stable when the scale is re-fitted.

**Options considered**
- **(A) The ordered pair of findings either side of the line; threshold recomputed as their midpoint at each fit.**
- **(B) A fixed scale-value threshold captured when the cut is set.**
- **(C) Threshold, with band assignments frozen at first assignment.**

**Decision ✅** — **(A).**

**Why** — A pairwise scale has no absolute origin, so a stored number means something only relative to the fit that produced it; after a refit the same threshold sits somewhere subtly different, and items cross it for reasons the rater never judged (B). (A) preserves the *meaning* of the judgment — "the boundary sits between this finding and that one" — which is what the rater actually decided, and it literally implements the source design's requirement that each cut be made with the findings on both sides visible: the cut **is** those findings. Items whose relation to the two anchors genuinely changed do move bands, which is honest rather than silent.

**Consequences / caveats** — A refit can invert an anchor pair, meaning the scale changed materially in that region; that is reported rather than silently re-sorted, and a human should look. This interacts with D2's freeze-and-propose rule: the assignment is frozen, and the recomputed threshold is what a proposed revision is measured against.

**Rule** — Acceptance criterion: a cut round-trips as an anchor pair and its threshold is recomputed from current scale values; an inverted anchor pair after a refit is reported by name. Enforced by test.

---

## D13 — Regularized Bradley-Terry

**Fork:** The maximum likelihood estimate diverges for any item that wins or loses *all* its comparisons. On a severity scale this is guaranteed, not exceptional: the most severe finding beats everything it meets and the least severe loses everything.

**Options considered**
- **(A) Small symmetric prior — λ pseudo-wins and λ pseudo-losses per item against a virtual opponent at the scale origin.**
- **(B) Detect and report** — refuse to emit values for unbounded items.
- **(C) Force extra comparisons** against mid-scale items until the item wins or loses one.
- **(D) Bounded iteration** — cap and use whatever value is reached.

**Decision ✅** — **(A)**, with λ = 0.5 as a declared constant.

**Why** — This is not an edge case to defend against but a certainty to design for, and it bites precisely at the Critical and Low ends whose bands matter most. Identifiability requires the comparison digraph to be *strongly* connected, not merely connected. (D) is the dangerous option: the iteration drifts to its cap and returns large arbitrary numbers that look like data, making the result depend on the cap rather than the judgments — satisfying the reproducibility requirement in letter while violating it in spirit. (B) is honest but declines to band exactly the findings the tool exists to rank, and the rater cannot fix it because the worst finding genuinely does lose to everything. (C) has no honest exit condition for the same reason. (A) guarantees finite estimates, converges, is fully deterministic, and its strength is a documented constant rather than a hidden fudge.

**Consequences / caveats** — The scale is slightly compressed at the extremes, by a bounded amount. That matters for reporting exact θ values and not at all for which side of a cut an item falls, which is the only thing severity output depends on. λ must be stated in the documentation, not buried in the fit.

**Rule** — Acceptance criterion: an item that wins every one of its comparisons receives a finite scale value and the fit converges within its iteration cap rather than reaching it. Enforced by test.

---

## D14 — Phase 1 re-cut to the size the sequencing decision assumed

> **The Rule below named an enforcer that does not exist (noted 2026-09-07).** It cited
> *"the spec linter's phase-tag agreement check"*; this repository has no linter and no
> `tools/` directory, so nothing enforced the placement and the specification drifted in
> **seven** places — three carrying `[P1]`, two in the phase-1 scope list, and two stating
> the guarantee without any tag at all. An audit found four of them by reading.
> `tests/test_constraints.py::test_no_phase_one_line_promises_a_feature_d14_deferred` is now
> the check that Rule described. The decision itself is untouched.

**Fork:** The consuming harness's D13 put this tool's build *in series ahead of its own*, justified on the MVP being small — "present two findings, record which is worse, persist, emit a total order, then three band cuts." What got specified is materially larger. Accept the delay, shrink phase 1, or unwind the serial order?

**Options considered**
- **(A) Cut phase 1 back toward the original MVP**, deferring the rest.
- **(B) Accept the larger tool and the longer serial delay.**
- **(C) Revisit the harness's D13 and run the two builds in parallel**, authoring findings without severities and backfilling.

**Decision ✅** — **(A).** Moved out of phase 1: **per-item standard errors** (Fisher information and its pseudo-inverse), the **session timer**, and the **comparisons-remaining estimate**. Retained: the regularized fit's scale values, cuts, band placement, the session API, a working comparison loop with undo and tie, appearance progress, and the severity writer.

**Why** — Standard errors are the largest and subtlest piece of work in the phase, and **nothing in phase 1 consumes them**: band placement compares scale values against cut thresholds, and D11's stopping rule counts appearances rather than reading precision. They exist to serve the phase-2 diagnostics. Deferring them removes exactly the component the build prompt singles out as dangerous — a wrong derivation produces plausible numbers rather than an error — from the phase that is blocking another project. (B) was rejected because the estimate the harness's sequencing rests on is not merely optimistic but now measurably wrong, and leaving it unaddressed makes a committed decision rest on a falsified premise. (C) remains available and is the right answer if the trimmed phase still runs long; it was not chosen now because the trimmed scope plausibly restores "days, not weeks".

**Consequences / caveats** — The spec's outcome mentions per-finding standard errors; that part of the outcome is now met at phase 2 rather than phase 1, which the phase tags say and the prose should not contradict. The harness's assumption that this tool is days of work is now testable rather than speculative, and the trimmed phase is what it should be tested against.

**Rule** — The phase-1 acceptance criteria contain no assertion about standard errors, and the phase-2 criteria contain the reproducibility assertion for them. Enforced by the spec linter's phase-tag agreement check plus a reading of the two criteria groups — the placement itself is *judgment, not checkable*.

---

## D15 — A scanner for cross-repository interface drift

**Fork:** Three findings have now come from one cause — nothing keeps this spec and the consuming harness's spec in agreement about their shared findings interface. D22 (harness) named this "judgment, not checkable". Repair the third instance, or build the detector?

**Options considered**
- **(A) Write the scanner now, before the build.**
- **(B) Write it after phase 1 ships.**
- **(C) Fix the instance and keep the check manual.**

**Decision ✅** — **(A).** A script taking both spec paths and asserting: both name the same six findings keys; both state severity is joined by id and never embedded; and neither asserts something about the other that the other's current version contradicts.

**Why** — The three instances are the format gap (harness D22), the tier gap (D10), and a stale assertion in this spec that the harness "currently names no format" — found in a sweep, after the harness had named one and pushed it. The governing rule is that at the third instance of a shape the deliverable is the detector rather than the repair, and the cheapest moment to lock a class is the change that empties it. (C) is the reasoning that permitted instances one and two. (B) defers past a build that touches both repositories, which is when a fourth instance is most likely.

**Consequences / caveats** — The scanner lives in the **harness** repository, because the harness owns the findings-document format (its D22 settled it) and the authoritative description belongs beside the checker. It takes both spec paths as arguments so it can be run from either working directory. That placement is a judgment call, and the alternative — duplicating it into both repos — was rejected as creating two sources of truth for one check.

**Rule** — The scanner exits non-zero on disagreement and is run before any commit that touches either spec's interface description. Enforced by the script itself; remembering to run it is *judgment, not checkable* until it is wired into a hook.

---

## D16 — The iteration cap, set from measurement

**Fork:** The MM iteration converges linearly. At a 10,000 cap it refused a *perfectly consistent* complete batch of about sixty items — not bad data, the best data possible. Raise the cap, accelerate the iteration, or accept the refusal?

**Options considered**
- **(A) Raise the cap, set from measurement.**
- **(B) Accelerate the iteration** (SQUAREM, Newton, or similar).
- **(C) Accept the refusal** and document the limit.

**Decision ✅** — **(A). `MAX_ITER` = 200,000**, with the measurements recorded at the constant.

**Why** — The surprising part is *which* input is slow. Realistic data — roughly ten appearances per item, pairs chosen near in rank, a rater who is not perfectly consistent — settles in **500–700 iterations** at both n=50 and n=200. The slow case is the opposite of pathological: a **complete graph with zero inconsistency**, where every item strictly beats every item below it and the scale wants to spread as wide as the prior allows. Measured: **8,708 iterations at n=50, 15,582 at n=75, 23,544 at n=100** — roughly linear in n.

So the old cap failed on cleanliness, not on noise. (C) would mean the tool refuses its own best-case input, which is indefensible. (B) is real engineering but solves a problem that does not occur: each iteration is now cheap (the sparse rewrite took a 1,000-item fit from 47 seconds to 0.14), so the cap is a guard against a fit that will *never* settle, not a time budget. Reaching 200,000 requires a shape no hand-rated corpus can produce — a complete graph beyond n≈500, which is over 125,000 human comparisons.

**Consequences / caveats** — A genuinely non-convergent fit now takes longer to fail. Acceptable: that path raises rather than returning, so a slow failure is still a correct one. If a future phase adds acceleration, this cap should be re-measured rather than carried forward.

**Rule** — Two acceptance criteria, enforced by test: a perfectly consistent complete batch of sixty items fits without hitting the cap and recovers the correct order; and realistic sparse input converges in under 5,000 iterations. Both would have failed before this change.

---

## D17 — Splitting a criterion that outlived its phase

**Fork:** The phase-1 acceptance criterion read *"an intransitive triad is accepted without error, and raises the misfit statistic for those items."* D14 later moved misfit statistics to phase 2 with the standard errors, leaving half of that criterion unreachable in the phase it was tagged to. Leave it, drop it, or split it?

**Options considered**
- **(A) Split it in two, one clause per phase.**
- **(B) Leave it and note the exception** when reporting the phase.
- **(C) Drop the misfit half** entirely.

**Decision ✅** — **(A).** Phase 1 asserts the triad is accepted rather than rejected and leaves the items indistinguishable; phase 2 asserts it raises their misfit.

**Why** — A criterion that cannot pass is worse than a missing one: it reads later as an unexplained failure rather than as a deliberate phase boundary, and the reader has no way to tell which. (C) would discard the assertion that matters most about intransitivity — that the tool *localizes* it — which is the whole reason the model was chosen over a sort. (B) relies on someone remembering the exception at exactly the moment the criteria list is being read by someone who was not here.

The general point is worth keeping: **when a decision moves work between phases, the acceptance criteria tagged to those phases move with it.** D14 moved the capability and left its criterion behind, which no linter can see — phase tags were internally consistent throughout, and the criterion was well-formed. Only running the criteria found it.

**Consequences / caveats** — Phase 1 passed 30 of 31 criteria before this split and 31 of 31 after; the change is bookkeeping, not a behavior fix. Phase 2 now inherits an assertion written before its implementation exists, which is the right direction for that dependency to run.

**Rule** — A phase is not reported complete until every criterion tagged to it has been run and passed. Where a criterion cannot pass, it is split or retagged before the phase is called done, never excused in prose. Enforced by judgment during the acceptance walkthrough — *not checkable*, since no linter can tell an unreachable criterion from a failing one.

---

## D18 — A changed finding is refused, and accepting is audited

**Fork:** A post-build sweep proved that reloading a revised findings document silently carried judgments made against the *old* wording onto the new. The requirement "a changed content hash creates a new item" was specified, carried an acceptance criterion, and was never implemented — and the phase was reported complete against it, wrongly. Implement it as written, or change it?

**Options considered**
- **(A) Refuse the load, name the changed findings, require an explicit flag; record the acceptance in the log.**
- **(B) Record the hash on every comparison so identity becomes `(id, hash)`** — the full original intent.
- **(C) Warn and continue.**
- **(D) Strict fork exactly as specified.**

**Decision ✅** — **(A).** `cj load` refuses, naming each changed finding and how many comparisons were made against its old text. `--accept-revisions` proceeds, and each acceptance appends a `revision` record to the log carrying finding id, both hashes, rater id and timestamp.

**Why** — The requirement as written was *wrong*, not merely unbuilt. Strict forking (D) is hostile to normal work: correcting a spelling mistake would orphan every judgment about that finding, which teaches a rater not to improve their own prose. (B) is the most correct model and carries the same practical objection at much greater cost. (C) is the silent behavior with a message attached, and a warning in a long load output is one nobody reads. Nothing can distinguish a typo from a rewrite automatically — so the tool refuses and a **human decides**, which is the only actor that can tell them apart.

The audit trail was the project owner's addition and it materially improves the option. Because acceptances live in the same append-only log as comparisons, **an acceptance changes the log hash** — so a severity file naming that hash is tied to a history that includes the acceptance rather than one that conceals it. Who, when, which finding, and which text it moved between are all recoverable.

Only *judged* findings are reported. A change to something nobody compared costs nothing, and flagging it would train a rater to wave the flag through by reflex.

**Consequences / caveats** — Amends the requirement from "SHALL create a new item" to "SHALL refuse unless explicitly accepted, and SHALL record the acceptance". Carrying judgments onto materially rewritten text is now *possible* — it is a human's call, made visibly, rather than the tool's, made silently.

**Rule** — Three acceptance criteria, enforced by test: a changed judged finding is refused by name; accepting appends a revision record with rater and both hashes; accepting changes the log hash. A fourth asserts an unjudged finding may change freely.

---

## D19 — The sequence number is re-read, never cached

**Fork:** An independent audit of 0.5.0 showed two `Store` handles on one store producing duplicate `seq` numbers. Because a retraction addresses a comparison *by* `seq`, and `active_comparisons()` filters with `seq not in retracted`, one retraction then withdrew **every** record sharing that number — silent, permanent corruption of the log this project calls the product. The cache was itself a 0.5.0 change, introduced to end an O(n²) session cost; the tests written in the same sweep covered the sequential-reopen case and missed the concurrent one.

**Options considered**
- **(A) Drop the cache; re-read the last sequence number from the log at each append.**
- **(B) Keep the cache and take an exclusive lock on the store directory** for the process lifetime, failing by name when it is held.
- **(C) Both.**

**Decision ✅** — **(A).** `_next_seq()` is now `_last_seq() + 1`, where `_last_seq()` seeks to the end of the log and reads back only the final record, growing its window until a complete line is in hand rather than assuming one fits.

**Why** — The correctness price of the cache was not worth its performance benefit, and (A) does not actually pay that benefit back: a tail seek is O(1) in the log's length, exactly like the cache and unlike the full-file parse the cache replaced. So the fix costs nothing measurable and removes the failure entirely for the case that is actually reachable — a `cj compare` open in one terminal while `cj load --accept-revisions` runs in another, or the pattern the tests themselves use.

(B) buys correctness under true simultaneity at the price of a lock file in a store layout the specification pins, plus a stale-lock recovery story after any crash — support burden for a scenario the spec never contemplates. (C) pays both prices.

**Consequences / caveats** — Two processes appending at the *same instant* can still collide; (A) closes interleaving, not simultaneity. This is recorded in *Not checked* rather than fixed, because the tool is single-rater by design in phase 1 and multi-rater analysis is phase 3, which is where a locking model belongs if one is ever needed.

**Rule** — Acceptance criterion, enforced by test: two `Store` handles opened on one store and appended to alternately produce strictly increasing, unique `seq` values, and retracting one leaves the other active. A second asserts a record longer than the tail window still resolves.

---

## D20 — Creating a store refuses to overwrite one

**Fork:** `cj init` against a populated store rewrote `meta.json` and `cuts.json`, destroying all three band cuts and the excluded-question summary, then exited **0** printing `created store at …`. The store's stated integrity property is *"Nothing is ever deleted; a retraction appends a record marking a prior comparison withdrawn."* This was a deletion — of the highest-value record in the store.

**Options considered**
- **(A) Refuse when `meta.json` exists; add `--force`, which still refuses once judgments exist.**
- **(B) Refuse unconditionally**, with no escape hatch.
- **(C) Make `init` idempotent** — create what is missing, leave what is there.

**Decision ✅** — **(A).**

**Why** — The three cuts are the only absolute judgments the tool ever asks for; the whole "exactly three, regardless of batch size" claim rests on them. Losing them to a command whose name reads as safe, with a success message and a zero exit, is the worst shape a data-loss bug can take.

(B) is nearly right and was close. It loses to (A) only on the false-start case — a store created at the wrong path, before any judgment — where forcing a user to reach for `rm -rf` is worse advice than giving them a flag. The flag is safe precisely because it *keeps* the refusal that matters: once a single judgment exists, `--force` refuses too, because re-initializing would leave those judgments referring to findings and cuts that no longer exist.

(C) is the most seductive and the most dangerous: "leave what is there" quietly becomes "and silently keep whatever was stale", which is how a store ends up half-belonging to two batches.

**Rule** — Acceptance criteria, enforced by test: `init` against an existing store refuses by name and changes no file; the cuts and the load summary both survive; `--force` re-initializes an unjudged store and still refuses a judged one.

---

## D21 — Connectivity is enforced, not merely reported

**Fork:** The requirement — *"WHEN the comparison graph contains more than one connected component, the system SHALL name the components and SHALL NOT report scale values as comparable across them"* — was half-built. `core/graph.py` existed, `cj status` warned, and a unit test covered `components()`. But `place()` and `cmd_fit` never asked, so `bands` and `export` both exited **0** having drawn a boundary between two findings that had never been compared. `DisconnectedComparisonsError` was defined for exactly this and was never raised: a defined-but-unraised error for an unimplemented requirement, the two pointing at each other.

**Options considered**
- **(A) Refuse in `place()`, and name the components in `fit`.**
- **(B) Warn everywhere and continue.**
- **(C) Band each component independently**, against its own cuts.

**Decision ✅** — **(A).** `Session.require_connected()` raises over the *judged* items; `bands` and `export` refuse and write nothing; `fit` prints the values and then names the groups it cannot compare. `set_cuts` refuses too, so the state is unreachable through the tool's own commands.

**Why** — Bradley-Terry estimates *differences*. Two groups never compared against each other have independent, arbitrary origins, so a band assigned across that gap reports the prior rather than a judgment — the identical failure the `unplaced` mechanism already exists to prevent, arriving by a different route. Severity is what the consuming harness joins on, so a prior-derived band is not a cosmetic defect: it is a wrong answer with provenance attached.

(B) is what `status` already did, and the audit's evidence is what it looks like in practice: the warning was in one command and the wrong answer came out of three others. (C) is a real design, and it is phase 3's — multi-rater analysis is where independent scales get reconciled, and inventing a partial version here would be a scale-per-component with no way to relate them.

**Consequences / caveats** — Connectivity is computed over items with at least one appearance. Counting unjudged items would report every part-way batch as disconnected, and a warning that fires constantly is one nobody reads. A test asserting the old behavior — the warning firing on a batch with *no* comparisons at all — was rewritten: it had been passing while saying something false.

**Rule** — Acceptance criteria, enforced by test: `bands` and `export` over a disconnected graph refuse by name and write nothing; `fit` names the components; `status` names them and identifies members.

---

## D22 — A finding removed from the document is refused, like a changed one

**Fork:** D18 guards findings whose *text* changed. Nothing guarded findings that had *vanished*. `put_findings` replaces the index wholesale, so deleting one line from the findings file dropped that finding from the index while its comparisons stayed in the log; the fit then skipped them and the surviving partner's appearance count silently fell. A judgment a human made was erased by an edit to a different line of the file.

**Options considered**
- **(A) Refuse the load, naming each removed judged finding and its comparison count; a distinct `--accept-removals` records the removal in the log.**
- **(B) Reuse `--accept-revisions`** for both.
- **(C) Retract the orphaned comparisons automatically.**
- **(D) Leave it — a deletion is deliberate by definition.**

**Decision ✅** — **(A).**

**Why** — This is D18's harm through the adjacent door, and D18's reasoning transfers without modification: nothing can distinguish a deliberate cut from a stray edit, so the tool refuses and a **human** decides. (D) assumes the deletion was intended and read; the case that motivates the guard is precisely the one where it was not.

(B) is cheaper and wrong for a specific reason: a rater who has learned that `--accept-revisions` means "yes, I edited some wording" would use it reflexively, and it would then also wave through the loss of an entire item. Two flags because they are two different acceptances, and the more consequential one should not be reachable by habit.

(C) is a retraction the rater never made, recorded in their name in an append-only log. The log is the product; nothing may write a judgment into it on a human's behalf.

**Consequences / caveats** — Adds `RemovalAccepted` to the log's record kinds and `Removal` to the pending-report types, mirroring `RevisionAccepted`/`Revision`. `RevisionAccepted` also gains a `comparisons` field it should have had: the count at the moment of acceptance, which is what the human was shown, and which a recomputation would later disagree with.

**Rule** — Acceptance criteria, enforced by test: a judged finding removed from the document is refused by name with its comparison count and the judgments survive; an unjudged one may be removed freely; accepting appends a removal record with rater and count and changes the log hash.

---

## D23 — The run id is derived, not minted

**Fork:** The run id was specified in four places across the spec and the build prompt, carried a `[P1]` acceptance criterion, and was implemented nowhere. This is **D18's shape recurring inside the sweep that named it** — specified, criterion written, never built, and the phase reported 31 of 31 against it. What a run id *is* was never decided, which is most of why it was never built.

**Options considered**
- **(A) A content hash of the log hash, the anchor-set version and the cuts.**
- **(B) A UUID minted per export.**
- **(C) A monotonic counter in `meta.json`.**

**Decision ✅** — **(A).** `run_id()` hashes `(log_hash, anchor_set_version, each cut's name, anchors and calibration note)`, truncated to sixteen hex characters.

**Why** — It identifies the *result* rather than the act of exporting. Two exports over an unchanged log, anchor set and set of cuts carry the same id; changing any of the three changes it. That answers a question worth asking — "is this the same severity assignment I saw before?" — which neither (B) nor (C) can answer at all.

It is also the only option that leaves the determinism NFR intact. The spec says two fits are byte-identical *"excluding a run-metadata envelope"*; there was no envelope, so the byte-identity test passed trivially. (B) and (C) would both have forced that exclusion to be built and the test weakened. (A) needs neither.

The cuts are in the hash because they are **not in the log**. Three different boundaries over one set of judgments are three different results, and an id that could not tell them apart would be worse than none.

**Consequences / caveats** — A cosmetic edit to a calibration note changes the id. That is intended: the note is part of what the bands mean, and a band whose stated calibration has changed is not the same claim.

**Rule** — Acceptance criteria, enforced by test: the severity payload carries a run id; two exports over an unchanged log carry the same one, *including* the id; changing a cut changes it.

---

## D24 — The seam covers the whole tool, not only the comparison loop

**Fork:** The requirement says the system exposes **exactly one** session interface to presentation layers. `Session` had no way to set cuts, load findings or export, so `cli.py` reached into `session._store` and `session._fit()` five times and imported six core modules directly. The scan enforcing the seam globbed `ui/` only — so it was green, and blind to the one front end actually breaking the rule.

**Options considered**
- **(A) Widen the seam:** add `Session.load`, `set_cuts`, `fit`, `export`, `components`, `open`, `create`; widen the scan to every module outside `core/`, derived from the layout.
- **(B) Record an exception:** declare the seam covers the comparison loop only, and allow configuration commands to use core directly.

**Decision ✅** — **(A).**

**Why** — D3's whole purpose is that phase-1 work should not become throwaway when the web adapter arrives. Under (B) that adapter cannot set a cut without importing `Store` and `Cut` and writing to the store itself — re-solving, in a second front end, the operation this tool describes as its most important. That is throwaway work by construction, and D3 exists to prevent exactly it.

The scan is the part that keeps this closed, and it was the more interesting failure: **a mechanism whose coverage was narrower than the rule it enforced.** It is now derived from the package layout rather than listed, so a new front end cannot arrive outside it, and a companion test asserts the derived set actually contains both front ends. A second scan catches the reach-around an import scan cannot see — `session._store` in a presentation module.

**Consequences / caveats** — `core.errors` joins `session` and `models` in the allowed set. Catching a named refusal is part of the contract, not a reach around it; the alternative is a front end that cannot tell a refusal from a crash. `Session.record` accordingly raises `NothingToJudgeError` rather than a bare `ValueError`, which would have escaped the CLI's handler as a traceback.

**Rule** — Acceptance criteria, enforced by test: the seam scan's universe contains every presentation module and is derived from the layout; no front end imports a core module outside the allowed three; no front end touches a private session attribute; no module in the package raises a bare builtin error.

---

## D25 — The top cut must carry a calibration note

**Fork:** Cut calibration was `[P1]` and in scope — *"pinning at least the top cut to an absolute statement so the relative scale acquires an origin"* — and `Cut.calibration_note` existed, defaulted to `""`, round-tripped through the store, and was set by nothing. In production it was always empty. There was **no EARS requirement and no acceptance criterion** for it, which is why the phase-1 walkthrough could not have caught it: the in-scope list named it and nothing else did.

**Options considered**
- **(A) Build it: `cj cuts --critical-high-note`, required on the top cut, carried into the severity file.**
- **(B) Defer to phase 2**, amending the in-scope tag to `[P2]`.

**Decision ✅** — **(A).** `set_cuts` refuses without a non-empty note on the top cut; all three notes are surfaced in `cj cuts` output and published under `calibration` in the severity file.

**Why** — The build prompt names the exact failure it prevents: *"a pairwise scale is relative with no origin — the ordering can be internally perfect while the whole set sits a band too high."* Everything downstream inherits that offset, and the severity file the consuming harness joins on would carry no record that it might. Deferring means phase 1 ships severity files with an uncalibrated origin and nothing saying so.

Required rather than optional because an optional field on the path of least resistance is an empty field. It is required on the **top** cut only: that is what the in-scope line asks for, and demanding three notes to place three cuts is the kind of friction that gets worked around.

**Consequences / caveats** — Every existing call site that sets cuts must supply a note, including in tests. The note is part of the run id (D23), so editing it changes the id.

**Rule** — Requirement plus acceptance criteria, enforced by test: `cuts` without a top-cut note refuses and writes nothing; the note reaches the severity file under `calibration`.

---

## D26 — Validate, then write — everywhere

**Fork:** Three separate commands mutated the store *before* checking their input, in a tool whose stated failure model is that an unrecoverable failure *"halts with a named cause and writes nothing"*. `cj cuts` persisted a cut naming a nonexistent finding and then reported the error, leaving `bands`, `export` and `compare` all broken with nothing telling the user how to recover. Four ordinary error paths — a missing findings file, malformed YAML, a corrupt `meta.json`, a truncated log line — escaped as raw tracebacks. And a retraction naming no live comparison was accepted silently.

**Decision ✅** — Ordering inverted in every case, and every parse boundary now raises a named refusal: `_loads` wraps `json.loads`, `load_findings` wraps the read and `parse_findings` wraps `yaml.safe_load`, `write_severity_file` wraps the write, and `append_retraction` refuses a `retracts_seq` that names nothing live.

**Why** — These are one defect wearing four hats, and the store cases are the sharpest. `_append_log` flushes per record *specifically* so that a crash cannot lose a judgment — and a crash during that write is exactly what leaves a truncated final line, which was then unreadable with a stack trace pointing into the standard library. The one mechanism designed for crash-safety produced the one input the reader could not handle.

The refusals are raised at the parse boundary rather than translated in `main()`, because `core` should not depend on the CLI to be well-behaved: a web adapter would otherwise have to re-implement the same translation, and would get it subtly different.

A stray retraction is inert to the fit, which filters by a set. It is not inert to the log's **hash**, which is published as provenance in every severity file — so it changes the fingerprint of a history without changing what that history says.

**Consequences / caveats** — `evidence: []` is now refused too. Observation and consequence were guarded by name and evidence was not, leaving the one field the schema exists to preserve as the one that could be empty.

**Rule** — Acceptance criteria, enforced by test: `cuts` naming an unknown finding leaves `cuts.json` byte-identical; a missing findings file, malformed YAML, a corrupt `meta.json` and a truncated log line each exit non-zero with a named refusal and no traceback; a stray or repeated retraction is refused; empty evidence is refused.

---

## D27 — US spelling, so two repositories sharing an interface do not disagree about orthography

> **Superseded in part by D29 (2026-09-07).** The Rule below named a word list, and that list
> was exactly as wide as the sweep that built it. D29 replaces it with a set of shapes plus
> declared exceptions. The decision to convert stands unchanged; only what holds it does.

**Fork:** This repository was written in one variety throughout, and the consuming harness converted to US at `745d1a6`. An independent audit filed the difference as *"a cross-repository choice to make deliberately, not a defect"* — this repository being internally consistent, which is what a style rule strictly requires. Convert, or record the difference and keep it?

**Options considered**
- **(A) Convert this repository to US**, matching the harness.
- **(B) Keep the existing variety and record the choice as deliberate**, since internal consistency is the actual requirement and converting touches a field on a public dataclass.
- **(C) Convert prose only and leave identifiers**, treating names as API surface.

**Decision ✅** — **(A).** Case-preserving replacement across tracked text files, with identifiers renamed by hand wherever `_` blocks a word boundary. `LICENSE` is excluded as a legal text and `uv.lock` as generated package metadata.

**Why** — (C) is the option that sounds careful and is not. It leaves the repository mixed, which is the state a spelling rule exists to prevent, and it needs an exemption shaped like *"names"* — a boundary that grows to fit whatever is inconvenient to change. (B) was the audit's own framing and is defensible; it loses on the shared interface. A scanner compares this spec against the harness's field by field, the two decision records cross-cite each other by number, and a reader moving between them meets two spellings of one concept. Fifty-five lines now, against a difference that never resolves itself.

**Consequences / caveats** — `FitResult.regularization` is renamed and it is a field on a public dataclass. It appears in no severity file and in nothing the harness reads, which was checked before the rename rather than after.

**The guard found six sites the conversion had missed, on its first run.** A word-boundary replacement cannot reach inside `unrecognized` or `TestRegularization` — the same limit that forced the renames by hand — so the guard matches substrings instead, and is deliberately wider than the change it holds rather than exactly as wide. **The harness's own conversion is held by nothing at all**, which is the half of this decision it does not have.

**Rule** — `tests/test_constraints.py::test_no_other_variety_spelling_returns`, planted by `test_the_spelling_guard_finds_a_planted_word`. The word list is stored in US form and its pairs derived at runtime, because this module sits inside the scan's own scope and a written-out list would be found by the check that reads it; the one function that must write two of them out is exempted by `ast`, and the exemption is asserted to conceal exactly those two.

---

## D28 — Finishing means something different once cuts exist, and that is a second field rather than one that changes meaning

**Fork:** `Progress.complete` was computed from the appearance target in every mode. Once cuts exist the loop places a newcomer in about three comparisons and stops, so a fully placed item — `next_pair()` returning `None`, the item banded — was reported as an unfinished batch. The TUI compensated by reading `placing`; `cj status` did not, and printed `complete no` beside a name it had already placed. How should the seam say what finishing means?

**Options considered**
- **(A) A second field**, `items_unplaced`, naming the placement shortfall, with `items_below_target` keeping the meaning its name states.
- **(B) Make `items_below_target` mode-aware**, so it names whatever the current mode is short of.
- **(C) Repair `complete` only**, and leave both front ends to interpret the lists.

**Decision ✅** — **(A).**

**Why** — (B) gives a field a meaning that depends on another field, and a field read without checking the other one is read wrongly. Its name would also be false half the time, since a freshly placed newcomer genuinely *is* below the appearance target — that statement is true and simply not the criterion. (C) is what the audit asked for and it is not enough: `cj status` would stop saying `complete no` and go on printing `below target F-NEW` for an item it had just finished placing, which is the same wrong yardstick one line further down.

`_unplaced` is deliberately the same predicate `_placement_pair` selects on, so that "nothing left to judge" and "complete" cannot disagree. They disagreed because they were computed from different things.

**Consequences / caveats** — `cj status` gains a `mode` line, because a reader cannot otherwise tell which of the two criteria is in force, and they are not interchangeable. `Progress` gains a field rather than changing one, so nothing reading `items_below_target` today reads something different tomorrow.

**The acceptance criterion for this was ticked against a neighbor.** `test_finishing_placement_is_not_reported_as_an_appearance_target` asserts the *TUI widget's text* and never reads `Progress.complete`, so the spec's line — that after cuts exist the appearance target is not reported as the finishing condition — was satisfied in one front end while the seam and the CLI both still had the defect. That is the shape D26's closing note calls the audit's most useful finding, arriving one front end over.

**Rule** — `tests/test_session.py::test_a_finished_placement_reports_complete` and `tests/test_cli.py::test_status_reports_a_finished_placement_as_complete`, with `tests/test_session.py::test_an_unfinished_placement_is_not_reported_as_complete` as the control. All three were planted and observed to fire: restoring the old criterion fails the first two, and `complete=True` fails the third.

---

## D29 — A word list is only as wide as the sweep that built it

**Fork:** D27's guard was a list of the spellings one sweep had found. Working the audit's remaining items turned up an `-isation` form in the specification, an `-lling` form in a decision record, and eight more of the same shapes — every one of them in a file that sweep had already read, and every one invisible to the guard written from it. Widen the list, or stop keeping one?

**Options considered**
- **(A) Match the shapes** — the productive suffixes — with declared exceptions for the ordinary words that share them.
- **(B) Add the ten and keep the list**, which is the repair the finding literally asks for.
- **(C) Drop the guard**, and treat spelling as a reviewer's job, as the harness does.

**Decision ✅** — **(A).**

**Why** — (B) repairs the instance and leaves the mechanism, which is the shape this project keeps finding: a check whose coverage is set by whoever last looked rather than by the rule it enforces. The eleventh word would have gone the same way as the first ten. (C) is what the harness does, and is why the harness's own conversion is held by nothing at all.

The cost of (A) is a list of exceptions in place of a list of targets, and that trade is the decision. A bare `-ise` is what catches an infinitive; it is also the ending of a good deal of ordinary English, so the guard now declares that a handful of everyday words are not differences. **The exception list fails in the safe direction, which the list it replaces did not**: a missing exception is a build failure somebody resolves in a minute, while a missing target was silence.

**Consequences / caveats** — three defects were found in the pattern before it passed, and each is a way this kind of guard goes wrong. Unanchored, it found the `-our` shape inside `resource`. Anchored, it could no longer see inside identifiers — which is what the unanchored form had been for — so identifiers are now split on `_` and on camel humps before matching, rather than the anchors being given up. And the bare `-our` ending missed the adjectival form, because the suffix is not always word-final.

**The forms live in one function, which is this repository meeting an old problem for the third time.** A checker that reads text cannot spell out what it forbids: every form is written only inside `_other_variety`, the scan skips exactly that function by locating it with `ast`, and the exemption is asserted to conceal exactly the pairs the function has to name — bounded by what it hides rather than by how many lines it spans, since line count is not what makes an exemption dangerous.

**Rule** — `tests/test_constraints.py::test_no_other_variety_spelling_returns`, unchanged in name from D27. `test_the_spelling_guard_finds_a_planted_word` plants three of the shapes the list-based version missed, composed at runtime rather than written out, so a future word of the same shape is caught without anybody adding it anywhere.

---

## D30 — Ticks removed rather than made to mean something

**Fork:** The acceptance criteria carried thirteen ticks out of sixty-seven, produced by nobody and read by nothing. Several *unticked* criteria were implemented and tested, and the 0.6.0 entry that introduced the boxes cites *"a criterion ticked against something adjacent to it"* as the defect they were added to fix. Build the verifier that would make a tick mean something, or stop claiming one?

**Options considered**
- **(A) Remove the ticks**, and say in the section what a box is worth now.
- **(B) Build a verifier with spec anchors**, in the shape of the consuming harness's `verify_phase1.py`: each criterion names a test, a tool runs them, the ticks are computed.
- **(C) Anchor the phase-1 criteria only** and remove the rest of the boxes.

**Decision ✅** — **(A).**

**Why** — (B) is the right end state and the wrong next step. Sixty-seven criteria would each need an anchor, and the anchors are the part that goes stale. The harness's verifier earns its cost because phase 1 there is *closed* and its criteria are the closing argument; here the criteria are still being written, so the machine would buy a computed answer to a question that is still moving, and every future criterion would pay for it.

(C) splits the document into criteria that mean something and criteria that do not, without saying which is which where a reader meets them — the exact ambiguity a tick already had.

(A) is the only option that makes the document true today, and it makes (B) cheaper later rather than harder: a list with no ticks is a list of claims, and anchoring a claim is easier than first having to work out which ticks were wrong.

**Consequences / caveats** — the specification loses a signal, and gains nothing in its place. A reader asking *"is this built?"* is sent to the `**Rule**` line of the decision that introduced the criterion, which names a test. That is a worse index than a tick and a true one, which is the trade.

**The 0.6.0 entry is corrected by appending rather than by rewriting.** Its claim — that the criteria *"now carry checkboxes tied to named tests"* — was false when it was written, and a changelog edited to agree with a later decision stops being a record of what was believed at the time.

**Rule** — `tests/test_constraints.py::test_no_acceptance_criterion_carries_a_tick`, with `test_the_criteria_are_still_there_to_be_ticked` as its control: an empty document satisfies a no-ticks assertion exactly as a correctly unticked one does, so the remaining criteria are counted as well.

---

## D31 — The interface check is triggered from the repository that breaks it

> **Amended the same day.** The Consequences below say the dispatch *"fires only when a push
> changed something under `specs/`"*. That was true and it made the dispatch testable only by
> doing the thing it guards — a manual run skipped the job entirely, so the wiring could not be
> exercised on demand and the token's scope could not be proven without a spec push. A manual
> run now reaches the job and skips the narrowing, and a refused dispatch fails loudly rather
> than being read out of `gh`'s exit code by whoever is looking.

**Fork:** D15 built a scanner comparing this specification against the consuming harness's, and put it in the harness, where it runs in the harness's CI. So an edit *here* that breaks the shared findings interface passes here, and goes on passing until that repository happens to build. D15's Rule named the gap and left it: *"judgment, not checkable until it is wired into a hook."* Wire it — and if so, from which side?

**Options considered**
- **(A) Dispatch** — a push to `main` here asks the harness to run its own workflow.
- **(B) Check the harness out here** and run `tools/check_spec_interface.py` over both specs, so the failure lands in the build of the repository that caused it.
- **(C) Leave it**, recording that the wiring was considered and why it was not done.

**Decision ✅** — **(A).**

**Why** — (B) puts the failure where the breaking edit was made, which is the better place for it, and pays for that with a read token for a private repository *plus a copy of another project's tooling pinned in this one*. That second cost disqualifies it: the scanner would then exist in two versions, and the one running here would be the one nobody updates. That is D24's shape and this repository's own recurring finding — a check whose copy has drifted from the rule it was written for.

(A) keeps one scanner. The failure lands in the harness's build rather than in this one, which is worse for whoever pushed; the notification arrives in about a minute instead of in days, which is better for everyone.

(C) was defensible while nothing had been built. It stops being defensible once the trigger is nine lines of YAML.

**Consequences / caveats** — **the dispatch is not armed yet.** It reads `HARNESS_DISPATCH_TOKEN`, which needs `Actions: write` on the harness and nothing else, and which only the owner can create. Until that secret exists the step warns instead of failing, so a missing token does not turn every spec push red. It warns loudly, because a dispatch nobody notices is not wired at all.

It fires only when a push changed something under `specs/`. The scanner reads the two specifications and nothing else, so a code-only push would ask for a run whose answer cannot have changed — and a trigger that fires on everything is a trigger whose failures stop being read.

**Rule** — `tests/test_constraints.py::test_a_spec_push_asks_the_harness_to_check_the_interface`, which asserts the *wiring* and not the run: the job, the repository it names, the token it reads and its narrowing to `specs/` are each something a later edit could quietly drop, and each checkable from here. Whether a dispatch succeeds is not checkable from here, and is not claimed.

---

## D32 — Tests may know how it works; front ends may not

**Fork:** The suite reaches into `session._store` and `session._fit()` in 43 places, measured when this was written, and the seam scan covers `src/` only. An audit called that *"fine, but the convention is unstated"* — which is the whole problem. An unstated convention is a habit, and a habit is defended by whoever happens to notice.

**Options considered**
- **(A) State it, and finish the guard that was already half-enforcing it.**
- **(B) State it in prose only**, since the existing scan covers production code and that is the half that matters.
- **(C) Hold tests to the seam as well**, giving them a public way to construct the states they need.

**Decision ✅** — **(A).** Tests may reach past the seam, `src/` may not, and the check saying so now reads attributes as well as imports.

**Why** — (C) is the purist answer and it buys nothing here. A test that built a bootstrapped session through the public API alone would spend most of its length doing so, and the states worth testing are the awkward ones — a store with an inverted cut, a newcomer half-placed. Test code that knows the implementation is coupled to it; that is the cost, it is paid in tests needing an edit when internals move, and it is a cost that announces itself rather than accumulating quietly.

(B) would have left the guard where it was, and where it was is the interesting part. `test_front_ends_reach_core_only_through_the_session_interface` reads **imports**, and `session._store.cuts()` needs no import at all. A front end could take the store out of the session it was handed, put its contents in a widget, and pass a scan whose own docstring says that is precisely what must not happen — a guard narrower than the rule it enforces, which is this repository's most-repeated finding. **Planted and observed:** a reach added to `cli.py` fails the new check and passes the old one.

**Consequences / caveats** — the attribute check excludes `self._x`, because a class using its own internals crosses no seam, and dunders, because `__class__` is protocol rather than privacy. Both exclusions are planted rather than assumed.

The convention makes a promise about *test* churn rather than about production stability: when a core internal moves, tests break. That is intended, and it is the reason this seam is worth having in one direction and not in the other.

**Rule** — `tests/test_constraints.py::test_no_front_end_reaches_past_the_seam_by_attribute`, with `test_the_attribute_seam_check_finds_a_planted_reach` as its control.

---

## Not checked — as of 0.6.0 @ D26

*Refreshed after an independent audit of 0.5.0 by a session that had written none of this code. Three earlier entries were retired because the audit resolved them: cut inversion has now been observed through the front end rather than only constructed in tests, the uncovered-lines list was measured rather than recalled, and the O(n²) claim was corrected below. **The most useful thing the audit produced was not a finding but a shape:** of forty-one, none was a mistake in the mathematics — the part checked hardest — and the recurring failure was a guard whose coverage was narrower than the rule it enforced, green and blind at the same time.*

- **The terminal UI has still never been run by a human.** Its formatting, its delegation and now its three completion messages are tested, but nobody has watched it render. The audit found the "batch complete" message on an inverted cut by reading state, not by seeing it — which is exactly the kind of defect a human would have spotted in ten seconds and a test suite did not spot in a hundred and seventy.
- **`MAX_ITER` = 200,000 is extrapolated past n=100.** Measured at n=50, 75 and 100; the trend beyond that is inference. The audit accepted D16's figures rather than re-measuring them, so they have been confirmed by nobody.
- **`PLACEMENT_COMPARISONS` = 3 is a fixed count, not a measured optimum.** The adaptive version is phase 3; until then every placement spends exactly three judgments even when the first two settle it.
- **λ = 0.5 is conventional, not validated.** No sensitivity analysis was run; the claim that it does not change which side of a cut an item falls is reasoned rather than measured. The audit did not test it either.
- **The appearance target of 10 is the same untested estimate it was adopted from.** Its *arithmetic* is now measured — the audit ran 50 findings at target 10 and spent exactly 251 comparisons, landing on the spec's ~250 estimate. Whether ten appearances buys enough **reliability** is still untested, and that is the half the number was chosen for.
- **Interactive latency is unmeasured as a requirement.** The only performance NFR covers the fit (0.14 s against a five-second budget). The audit measured what a rater actually feels: 28 ms per keypress at 25 findings, 42 ms at 50, 64 ms at 100 — imperceptible at the ~70-finding corpus this targets. It is roughly linear in *n*, because `next_pair()` and `record()` between them re-read the log and the findings index about nine times per judgment. Extrapolated to 1,000 findings that is on the order of half a second per keypress, in a tool whose entire premise is that per-comparison friction cancels the method's benefit (D3). Not a phase-1 defect; a phase-2 requirement waiting to be written.
- **The remaining O(n²) is in the session, not the store.** D19 removed the store's, and the 0.5.0 changelog's claim that "the sequence counter is cached, ending an O(n-squared) session cost" was narrower than it read: one such cost ended, and the one above did not. Stated here so the next reader does not conclude the session is linear.
- **Concurrency beyond interleaved appends is unexplored.** D19 closes the reachable case — two handles appending in turn. Two processes appending at the *same instant* can still collide, and nothing in the store takes a lock. Demonstrated at its simplest by the audit; cross-process behavior is inferred from the same code path, not executed.
- **No literature review was performed.** The method and the ten-appearances figure come from general knowledge of comparative judgment practice, not from cited sources. The audit did not check them against sources either.
- **Whether bands are recomputed or frozen after a refit was decided in principle** (freeze, propose revisions) and is still not written as a requirement.
- **Carrying judgments across an accepted revision (D18) or a removal (D22) is unmeasured.** Nobody knows how far text can drift before old judgments stop meaning anything, and the tool tells a rater only that the hashes differ — not how large the change was.
- **`anchor_set_version` cannot be set by any caller.** `Store.create` accepts it, nothing passes it, and `cj init` has no flag, so it is permanently `"1"` while the severity file publishes it as provenance and the run id hashes it. Reserved until anchor import/export lands in phase 2; a version that never changes is honest only while there is one anchor set.
- **The venv runs Python 3.14; mypy targets 3.12.** CI now runs 3.12 and 3.13 (D-none, part of the audit sweep), so the gap is narrowed but not closed: the interpreter development actually happens on is in neither matrix row.
- **The uncovered 2%** is textual's `compose`, the `compare` subcommand's launch path, the `__main__` guard, and two OSError branches reachable only by revoking read permission mid-run. Measured, not recalled — the previous entry named three things when there were five.
- **Not swept this pass: the harness project.** Its specs were not re-read, and the audit explicitly did not read that repository, so the cross-repository interface claims (D5, D10, D15) are unverified by both passes. The scanner D15 describes lives there and neither of us ran it.
- **This audit was not itself audited.** Every finding here was reproduced before acting on it — the eight executable ones against the audit's own script, the rest by reading the code it named — but a second independent pass would be looking at work that has now been reviewed twice by the same two perspectives.

## Document status

Decisions **D1–D33** recorded. The most consequential is **D2**, which overturns the source design's central algorithmic choice; **D5** additionally settles a gap in a second project's specification, which must be amended to match.

## D33 — A severity row was a conclusion with its basis stripped off

**Fork:** The consuming project asked for two things this file could not say, on the same day and
for different reasons, and both are about a row recording a verdict without recording what stands
behind it.

**The first is D18's hash, computed and never exported.** `content_hash` has existed since the
beginning and does real work: `load` refuses when a judged finding's text has changed, because the
tool cannot tell a corrected typo from a rewrite. But the hash lives in the store, and the store is
local — the harness gitignores it, correctly, since a local judgment log is not a repository
artifact. So the *exported* file records a band and not the text it banded, and a finding edited
after export keeps a band describing wording nobody compared.

That is not a hypothetical. On 2026-09-07 a repository-wide spelling conversion in the harness
edited three judged findings, one British spelling inside each, and **nothing
noticed for four days**.
The refusal fired correctly the moment somebody ran `load`, which is exactly the problem: the edits
that cause this are cross-cutting sweeps, and a sweep is the edit where nobody is thinking about
severity and nobody runs this tool.

**The second is that ties are invisible in the output.** A tie is a judgment and this tool keeps it
in the log, deliberately — `winner_loser()` returns `None` rather than an arbitrary order so a
caller cannot silently treat one as decided. The fit then excludes ties, because they carry no
information about ordering. Both are right. The consequence is that **ten appearances with eight
ties is a band placed on two results**, and the exported row says `theta` and nothing else.

The harness met that consequence head-on: placing one new finding against the cuts moved a cut
anchor by **forty-six times the median shift across every other finding**, because that anchor was
three wins, no losses and eight ties — undefeated on three informative comparisons, which a
regularized fit pushes upward with only λ to restrain it. Three unrelated findings changed band.
Nothing in the file it reads could have warned it.

**Options considered.**

- **(A) Export both**: `content_hash`, `appearances` and `informative` on every row, at schema 2.
- **(B) Export the hash only**, and leave determinacy to `status`.
- **(C) Export neither and document the store as the place to look**, which is the standing answer.

**Decision: (A).**

**Why not (C)** — it is what was in place, and the four-day gap is its measurement. A detector that
only fires when somebody thinks to run it does not cover the case where nobody is thinking about
this tool at all.

**Why not (B)** — the two failures are independent and a reader cannot derive either from the other.
A hash says the text has not moved; it says nothing about whether the band rests on two comparisons
or ten. The harness needed both on the same afternoon.

**Why the version moves.** The consuming contract says extra fields are tolerated and a missing one
is a break, so absence is legal — and without a version bump a consumer cannot distinguish a file
that predates these fields from a tool that never wrote them, which means any check for them would
have to tolerate absence and would therefore assert nothing. `schema_version` is `2`.

**Consequences / caveats** — `run_id` does **not** move, deriving from the log hash, the anchor set
and the cuts rather than from the file's fields, so the byte-identity guarantee holds across the
bump and a consumer's stored run id still resolves. `build_payload` now refuses to write a row for a
finding the store does not hold, rather than emitting a band with an unknown basis, which is the row
this entry exists to make impossible. **The counts describe the log at export time**: a later
retraction changes them, which is correct and is why they are read from the store rather than passed
in by a caller who might hold a stale view.

**Rule** — enforced by test: every exported row's `content_hash` equals the store's hash for that
finding, `appearances >= informative >= 0`, no banded row has zero appearances, and the payload
declares `schema_version` 2.


Spec: `specs/comparative-judgment.md`. Build prompt: `specs/comparative-judgment.build-prompt.md` (phase 1, frozen).

Any new fork encountered during the build is appended here in the same shape, and from **D9** onward each entry ends with a `**Rule**` line naming what enforces it. Numbering continues from **D34**. Both figures, and the gaplessness of the sequence between them, are asserted by `tests/test_constraints.py::test_the_decision_record_states_its_own_high_water_mark` — so this section is the maintained copy rather than a remembered one, and the header no longer keeps a second.

# comparative-judgment

Assign severity by answering *"which of these two is worse?"* — never by picking a number on a
scale.

> **Status: phase 1 in progress.** Usage documentation lands at the end of this phase. The sections
> below are settled decisions rather than descriptions of code, which is why they are here first.

## Why pairwise

A written definition tells a rater what the severity levels *mean*. It does not tell them how to
*decide* a borderline case — and almost every case is borderline. Rating an item in isolation
requires holding the entire scale and every prior rating in working memory, which is precisely what
erodes across a long session. A pairwise judgment is local: it needs only the two items in view.

That asymmetry is why comparative judgment reliably outperforms absolute rubric grading on
inter-rater reliability in educational assessment, and it is the property this tool is built on.

No disagreement rate is quoted anywhere in this project, deliberately. A figure from one reviewer
pair on one corpus in one session cannot be generalised, and quoting it invites a question that a
single observation cannot answer.

## Your findings text may be sensitive

The store holds the full text of every finding you load — observations, verbatim evidence,
consequences. For anyone other than this project's author those may be **real production defect
descriptions**.

- **There is no implicit store path.** Every invocation names its store; running without one fails
  by name rather than writing somewhere you did not choose.
- **The conventional location used by these docs and examples is `.cj-store/`**, and it is
  gitignored — so following the examples cannot produce a file that quietly wants committing.
- Nothing is transmitted anywhere. The tool makes **no network request of any kind**, and a test
  fails the build if a socket is opened.

## No language model, anywhere

Not a simplification to be revisited later — a permanent architectural property. This tool's output
is the ground truth against which an LLM judge is measured elsewhere; a model inside it would make
the measuring instrument depend on the thing being measured. A test scans the lockfile and fails if
a model dependency appears.

## How severity is derived

Comparisons accumulate in an append-only log. A **regularised Bradley-Terry** model fits a scale to
them, and three **band cuts** — the only absolute judgments the tool ever asks for, regardless of
how many findings you have — divide that scale into Critical / High / Medium / Low.

Two details worth knowing before you read numbers out of it:

- **Regularisation strength is λ = 0.5**, applied as pseudo-wins and pseudo-losses per item against
  a virtual opponent at the scale origin. Without it the estimate diverges for any item that wins or
  loses *all* its comparisons — guaranteed at both ends of a severity scale. It compresses the
  extremes by a bounded amount, which affects reported scale values and not which side of a cut an
  item falls.
- **A cut is stored as the two findings either side of it**, not as a number. A pairwise scale has
  no absolute origin, so a stored threshold means something only relative to the fit that produced
  it.

## Licence

Apache-2.0 — see [LICENSE](LICENSE).

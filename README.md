# comparative-judgment

Assign severity by answering *"which of these two is worse?"* — never by picking a number on a
scale.

## Using it

```bash
cj init   --store .cj-store
cj load   --store .cj-store --findings findings.yaml
cj compare --store .cj-store --rater you        # the comparison loop
cj status --store .cj-store                     # progress and measured costs
cj fit    --store .cj-store                     # the scale
cj cuts   --store .cj-store --critical-high F-01:F-03 \
                            --high-medium  F-03:F-02 \
                            --medium-low   F-02:F-05
cj bands  --store .cj-store                     # each finding's band
cj export --store .cj-store --out severity.json
```

In the comparison loop: `←`/`a` and `→`/`d` choose, `t` marks a pair too close to call, `u` undoes
the last judgment, `q` quits. Nothing needs saving — every judgment is on disk before the next pair
appears, so quitting and resuming is indistinguishable from never having stopped.

### What a findings document looks like

```yaml
findings:
  - id: F-01
    call_ref: CALL-02
    observation: The agent confirmed a completed seat exchange before the tool ran.
    evidence:
      - "line 42: I've gone ahead and moved you to row C."
      - "line 51: exchange_seats -> ERROR ineligible_after_doors"
    consequence: The caller left believing they had different seats; they do not.
    detectable_by: assert      # assert | judge | human
    tier: defect               # defect | question
```

`evidence` is a **list** so fragments from different points in a call stay visibly separate. `tier:
question` entries are excluded from scoring and the count is reported — they are non-defects with no
consequence to compare against, and rating one would put it into the anchor set where it would
distort every later placement. A `severity` field here is **rejected**: severity is this tool's
output, joined back by `id`.

### What it produces

A run over five findings, ten comparisons:

```
finding              theta   app    W    L    T
F-01                2.1416     4    4    0    0
F-03                0.9481     4    3    1    0
F-02               -0.0000     4    2    2    0
F-05               -0.9481     4    1    3    0
F-04               -2.1416     4    0    4    0
```

Note `F-01` won every comparison and `F-04` lost every one, and both still have finite values. That
is the regularisation below; without it those two would have run away to infinity, and they are
precisely the Critical and Low findings whose bands matter most.

The severity file names the run it came from, so a value can always be traced back:

```json
{
  "schema_version": "1",
  "anchor_set_version": "1",
  "comparison_log_hash": "01ab8a59dbd7a002…",
  "severities": [{ "id": "F-01", "severity": "critical", "theta": 2.14 }],
  "unplaced": []
}
```

`unplaced` lists findings nobody compared. They get no band rather than a defaulted one: banding
them would report the prior as though it were a judgment.

---


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

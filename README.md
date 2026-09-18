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

The note on the top cut is required, and it is the one thing here that is not a comparison. A
pairwise scale has no origin: the ordering can be internally perfect while the whole set sits a band
too high, and every consumer of the severity file would inherit that offset with nothing recording
it. The note is what the boundary was drawn against, and it travels with the result.

Three commands refuse rather than proceed, and each refusal is the point:

```bash
cj init --store .cj-store          # refuses if a store is already there --
                                   # creating would wipe the three band cuts
cj init --store .cj-store --force  # re-initializes, and still refuses once
                                   # any judgment has been recorded
cj load --store .cj-store --findings findings.yaml --rater you --accept-revisions
cj load --store .cj-store --findings findings.yaml --rater you --accept-removals
```

The last two are for when the findings document has moved under judgments already made — text
edited, or an entry deleted. Neither is waved through: the load is refused, each affected finding is
named with how many comparisons were made against it, and accepting appends a record to the same
append-only log as the comparisons. So an acceptance **changes the log hash**, and a severity file
naming that hash is tied to a history that includes it rather than one that conceals it. The rater
id is required on those paths — an audit record that cannot say who is answering three of its four
questions.

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
is the regularization below; without it those two would have run away to infinity, and they are
precisely the Critical and Low findings whose bands matter most.

The severity file names the run it came from, so a value can always be traced back:

```json
{
  "schema_version": "3",
  "anchor_set_version": "1",
  "comparison_log_hash": "01ab8a59dbd7a002…",
  "run_id": "4f2c9a10be77d3e5",
  "calibration": {
    "critical_high": "Critical means the caller acts on a false statement about their booking."
  },
  "cuts": [
    { "name": "critical_high", "above_id": "F-01", "below_id": "F-02", "gap": 0.61, "between": [] },
    { "name": "high_medium", "above_id": "F-02", "below_id": "F-03", "gap": 1.12, "between": [] },
    { "name": "medium_low", "above_id": "F-03", "below_id": "F-04", "gap": 0.95, "between": [] }
  ],
  "severities": [
    {
      "id": "F-01",
      "severity": "critical",
      "theta": 2.14,
      "content_hash": "9b1f…",
      "appearances": 10,
      "informative": 8
    }
  ],
  "unplaced": []
}
```

`run_id` is **derived, not minted** — a hash of the log, the anchor-set version and the three cuts.
So it names the *result* rather than the act of exporting: two exports over an unchanged history
carry the same id, and changing a single cut changes it. That also means the whole file is
byte-identical across runs, with no field carved out as an exception.

`unplaced` lists findings nobody compared. They get no band rather than a defaulted one: banding
them would report the prior as though it were a judgment. For the same reason, a severity file is
refused outright when the findings you judged fall into groups with no comparison between them —
Bradley-Terry estimates *differences*, so a boundary across that gap separates items on the strength
of the prior rather than of anything you decided.

`cuts` says how each boundary sits on the fit the file came from: its two anchors, the gap between
them, and any banded finding that has come to lie strictly between them. A cut is drawn between
neighbors, and a later refit can move them apart; the findings that end up inside are banded by the
midpoint rather than by anything you judged against that boundary. The tool reports that and refuses
nothing for it (an inverted cut is refused, a widened one is yours to judge), and the list is
computed from the rows in the same file, so a consumer can check it from `theta` alone.

---


## Why pairwise

A written definition tells a rater what the severity levels *mean*. It does not tell them how to
*decide* a borderline case — and almost every case is borderline. Rating an item in isolation
requires holding the entire scale and every prior rating in working memory, which is precisely what
erodes across a long session. A pairwise judgment is local: it needs only the two items in view.

That asymmetry is why comparative judgment reliably outperforms absolute rubric grading on
inter-rater reliability in educational assessment, and it is the property this tool is built on.

No disagreement rate is quoted anywhere in this project, deliberately. A figure from one reviewer
pair on one corpus in one session cannot be generalized, and quoting it invites a question that a
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

Comparisons accumulate in an append-only log. A **regularized Bradley-Terry** model fits a scale to
them, and three **band cuts** — the only absolute judgments the tool ever asks for, regardless of
how many findings you have — divide that scale into Critical / High / Medium / Low.

Two details worth knowing before you read numbers out of it:

- **Regularization strength is λ = 0.5**, applied as pseudo-wins and pseudo-losses per item against
  a virtual opponent at the scale origin. Without it the estimate diverges for any item that wins or
  loses *all* its comparisons — guaranteed at both ends of a severity scale. It compresses the
  extremes by a bounded amount, which affects reported scale values and not which side of a cut an
  item falls.
- **A cut is stored as the two findings either side of it**, not as a number. A pairwise scale has
  no absolute origin, so a stored threshold means something only relative to the fit that produced
  it.

## What this tool depends on, and what depends on it

This tool stands alone: nothing in `src/` imports another project, and the whole comparison loop
runs against a findings file and a store. The coupling is at the edges, and it runs both ways.

| | |
|---|---|
| [`voice-agent-eval-harness`](https://github.com/hmbseaotter/voice-agent-eval-harness) **reads this tool's output** | it joins the severity file onto its findings by `id`. Six of the eight keys in a findings entry cross that boundary; `call_ref` and `owner` are the harness's own and this tool never sees them |
| **this tool reads nothing of the harness at runtime** | but the two specifications describe one interface, and they can drift apart without either build noticing |

`tools/check_spec_interface.py` compares the two specifications field by field — and it lives in the
harness, so it runs when *that* repository builds. A change made here would sit unchecked until then.
So a push to `main` here that touches `specs/` asks the harness to run it now (D31), which is what
`HARNESS_DISPATCH_TOKEN` is for.

## The token this repository needs

`HARNESS_DISPATCH_TOKEN` is the only secret here.

| secret | grants | on | so that |
|---|---|---|---|
| `HARNESS_DISPATCH_TOKEN` | `Actions: Read and write` | `voice-agent-eval-harness` | a spec change here can start the harness's workflow, which runs the interface scanner |

`Actions: Read and write` is the narrowest grant that can start a workflow — GitHub offers no
write-only option for it, and read alone cannot dispatch.

**Making one.** GitHub → your avatar → **Settings** → **Developer settings** → **Personal access
tokens** → **Fine-grained tokens** → **Generate new token**, or go straight to
<https://github.com/settings/personal-access-tokens/new>.

| field | value |
|---|---|
| Resource owner | `hmbseaotter` |
| Repository access | **Only select repositories** → `voice-agent-eval-harness` |
| Permissions | **Repository permissions** → `Actions: Read and write`, and nothing else |
| Expiration | your choice — **write the date down** |

Copy it on the page that follows; GitHub will not show it again. Then install it here: this
repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**,
named exactly `HARNESS_DISPATCH_TOKEN`.

**On the day it expires**, a spec change here stops asking the harness to check the interface, and
the two specifications can drift with both builds green. The dispatch step fails loudly and names the
permission needed, so the lapse shows up as a red build rather than as silence — which is the whole
reason it fails instead of warning.

**The token installed now expires on 2026-11-06.** It was created on 2026-09-07 with a 60-day
lifetime, which is the runway to finish the harness and the projects around it and make them public
— a choice for that purpose, not a recommendation. Whoever replaces it updates this date. It is the
author's token and reaches only the author's repository, so it works for nobody else: running these
repositories under another account means creating all three tokens there, by the steps above and the
harness's map, and pointing the repository names the workflows spell out — here,
`hmbseaotter/voice-agent-eval-harness` in `.github/workflows/checks.yml` — at that account's copies.

**Three repositories share three tokens, and the map of which grants what lives in the harness's
README**, under *Access: three fine-grained tokens*. It is there rather than here because the harness
is the hub: it is the only one of the three that talks to both others.

## License

Apache-2.0 — see [LICENSE](LICENSE).

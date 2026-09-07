"""Connected components over the comparison graph.

Why this exists even though the fit always returns numbers: the regularization
(D13) connects every item to a virtual opponent at the scale origin, so the model
is always identifiable and always converges. That makes the estimates *defined*
but not always *evidentially comparable* — two groups never judged against each
other have their relative position set by the prior rather than by anything a
rater said. Reporting those as comparable would present an artefact of the prior
as a finding.

Ties are deliberately excluded from the linkage. A tie is real information about
closeness, but it never enters the win matrix, so the fit cannot use it: two
groups joined only by ties are joined by nothing the estimate can see.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from comparative_judgment.core.models import Comparison


class _UnionFind:
    """Path-compressed union-find over string ids.

    Iteration order of the parent map never affects the result: components are
    returned sorted, and membership is set-based.
    """

    def __init__(self, items: Iterable[str]) -> None:
        self._parent: dict[str, str] = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression, second pass.
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            # Deterministic merge direction: the lexicographically smaller root
            # wins, so the same input always produces the same parent map.
            if right_root < left_root:
                left_root, right_root = right_root, left_root
            self._parent[right_root] = left_root


def components(
    item_ids: Sequence[str], comparisons: Iterable[Comparison]
) -> tuple[tuple[str, ...], ...]:
    """Group items by whether a decided comparison links them.

    Returns components sorted by their smallest member, each internally sorted,
    so the output is stable across runs and insertion orders.
    """
    union_find = _UnionFind(item_ids)
    known = set(item_ids)
    for comparison in comparisons:
        pair = comparison.winner_loser()
        if pair is None:  # a tie links nothing the fit can use
            continue
        winner, loser = pair
        if winner in known and loser in known:
            union_find.union(winner, loser)

    grouped: dict[str, list[str]] = {}
    for item in item_ids:
        grouped.setdefault(union_find.find(item), []).append(item)

    return tuple(tuple(sorted(members)) for _, members in sorted(grouped.items()))


def is_connected(item_ids: Sequence[str], comparisons: Iterable[Comparison]) -> bool:
    """True when every item is linked to every other by decided comparisons."""
    return len(components(item_ids, comparisons)) <= 1

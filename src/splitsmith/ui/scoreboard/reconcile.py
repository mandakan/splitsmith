"""Name-based reconciliation of local shooters to scoreboard competitors.

Pure and I/O-free: the endpoint layer feeds it lists and persists the applied
mapping. Produces proposals a human confirms; never auto-applies.
"""

import unicodedata

from pydantic import BaseModel


class LocalShooter(BaseModel):
    slug: str
    name: str
    division: str | None = None


class CompetitorRef(BaseModel):
    competitor_id: int
    shooter_id: int
    name: str
    division: str | None = None


class LinkProposal(BaseModel):
    slug: str
    competitor_id: int | None = None
    shooter_id: int | None = None
    competitor_name: str | None = None
    score: float = 0.0
    ambiguous: bool = False


def _norm(value: str) -> frozenset[str]:
    folded = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    return frozenset(t for t in ascii_only.lower().split() if t)


def _name_score(a: str, b: str) -> float:
    ta, tb = _norm(a), _norm(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)  # Jaccard over name tokens


def propose_shooter_links(local: list[LocalShooter], competitors: list[CompetitorRef]) -> list[LinkProposal]:
    proposals: list[LinkProposal] = []
    for shooter in local:
        scored = sorted(
            (
                (
                    _name_score(shooter.name, c.name)
                    + (0.1 if shooter.division and shooter.division == c.division else 0.0),
                    c,
                )
                for c in competitors
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best = scored[0] if scored else None
        if best is None or best[0] < 0.5:
            proposals.append(LinkProposal(slug=shooter.slug))
            continue
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        proposals.append(
            LinkProposal(
                slug=shooter.slug,
                competitor_id=best[1].competitor_id,
                shooter_id=best[1].shooter_id,
                competitor_name=best[1].name,
                score=best[0],
                ambiguous=(best[0] - runner_up) < 0.15,
            )
        )
    return proposals

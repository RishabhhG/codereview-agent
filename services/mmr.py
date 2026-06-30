import numpy as np
import logging
from services.embeddings import embed_query, embed_text

logger = logging.getLogger(__name__)


def _cosine_sim(a: list[float], b: list[float]) -> float:
    a, b = np.array(a), np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


async def apply_mmr(
    query_embedding: list[float],
    candidates: list[dict],
    final_k: int = 5,
    lambda_param: float = 0.7  # higher = favor relevance, lower = favor diversity
) -> list[dict]:
    """
    Re-rank candidates to balance relevance with diversity.
    Needs each candidate's embedding — fetch it if not already attached.
    """
    if len(candidates) <= final_k:
        return candidates

    # We need embeddings for diversity comparison — re-embed chunk texts
    # (In production you'd store+fetch embeddings instead of re-computing)
    for c in candidates:
        if "embedding" not in c:
            c["embedding"] = await embed_text(c["chunk_text"])

    selected = []
    remaining = candidates.copy()

    # Start with the most relevant chunk
    remaining.sort(key=lambda c: c["similarity"], reverse=True)
    selected.append(remaining.pop(0))

    while len(selected) < final_k and remaining:
        best_score = -float("inf")
        best_idx = 0

        for i, candidate in enumerate(remaining):
            relevance = candidate["similarity"]
            # Max similarity to anything already selected
            max_sim_to_selected = max(
                _cosine_sim(candidate["embedding"], s["embedding"])
                for s in selected
            )
            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim_to_selected

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        selected.append(remaining.pop(best_idx))

    return selected
"""Optimal String Alignment (OSA) distance and thresholded verification.

# Optimal String Alignment (OSA) Distance

This module implements thresholded Optimal String Alignment distance, also known
as restricted Damerau-Levenshtein distance. OSA admits four basic single-character
operations:
1. Insertion (cost 1)
2. Deletion (cost 1)
3. Substitution (cost 1)
4. Adjacent Transposition (swap of two adjacent characters, cost 1)

In OSA, each substring may be edited at most once.

## Thresholding and Early Rejection

To avoid costly full matrix evaluations across candidate pairs:
1. Early length rejection: if abs(len(s1) - len(s2)) > limit, return limit + 1.
2. Identical strings return 0 immediately.
3. Banded dynamic programming: for maximum distance `k = limit`, only diagonals
   where |i - j| <= k need to be explored.
4. Pruning: if in any row all cells within the band exceed `limit`, search halts
   early and returns limit + 1.
"""


def osa_distance_reference(s1: str, s2: str) -> int:
    """Compute exact unbounded Optimal String Alignment distance between s1 and s2.

    Used as an unoptimized reference for testing banded and thresholded distance.
    """
    n = len(s1)
    m = len(s2)
    if n == 0:
        return m
    if m == 0:
        return n

    # Matrix (n + 1) x (m + 1)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,      # deletion
                d[i][j - 1] + 1,      # insertion
                d[i - 1][j - 1] + cost,  # substitution
            )
            if (
                i > 1
                and j > 1
                and s1[i - 1] == s2[j - 2]
                and s1[i - 2] == s2[j - 1]
            ):
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)  # transposition

    return d[n][m]


def thresholded_osa_distance(s1: str, s2: str, limit: int) -> int:
    """Compute Optimal String Alignment distance between s1 and s2 bounded by limit.

    If the edit distance exceeds limit, returns limit + 1 early.
    Employs early length rejection, banded traversal (|i - j| <= limit),
    and row-minimum pruning.

    Args:
        s1: First string.
        s2: Second string.
        limit: Maximum allowable edit distance (non-negative integer).

    Returns:
        Exact distance if <= limit, otherwise limit + 1.
    """
    if limit < 0:
        return limit + 1

    if s1 == s2:
        return 0

    n = len(s1)
    m = len(s2)

    # Early length rejection: insertions/deletions alone exceed limit
    if abs(n - m) > limit:
        return limit + 1

    if n == 0:
        return m if m <= limit else limit + 1
    if m == 0:
        return n if n <= limit else limit + 1

    infinity = limit + 1
    k = limit

    # We maintain 3 rows for OSA (current row i, previous row i-1, and i-2 for transposition)
    # Row indices represent j from 0 to m.
    # To keep banded access correct, cells outside the band remain infinity.
    row_prev2 = [infinity] * (m + 1)
    row_prev = [infinity] * (m + 1)
    row_curr = [infinity] * (m + 1)

    # Base row i = 0
    for j in range(min(m + 1, k + 1)):
        row_prev[j] = j

    for i in range(1, n + 1):
        row_curr = [infinity] * (m + 1)
        j_min = max(1, i - k)
        j_max = min(m, i + k)

        # Base column j = 0 for row i
        if i <= k:
            row_curr[0] = i
            row_min = i
        else:
            row_min = infinity

        s1_char = s1[i - 1]
        s1_prev_char = s1[i - 2] if i > 1 else None

        for j in range(j_min, j_max + 1):
            s2_char = s2[j - 1]
            cost = 0 if s1_char == s2_char else 1

            # Standard Levenshtein transitions
            del_cost = row_prev[j] + 1
            ins_cost = row_curr[j - 1] + 1
            sub_cost = row_prev[j - 1] + cost

            val = del_cost if del_cost < ins_cost else ins_cost
            if sub_cost < val:
                val = sub_cost

            # OSA adjacent transposition
            if i > 1 and j > 1:
                if s1_char == s2[j - 2] and s1_prev_char == s2_char:
                    trans_cost = row_prev2[j - 2] + 1
                    if trans_cost < val:
                        val = trans_cost

            if val > infinity:
                val = infinity

            row_curr[j] = val
            if val < row_min:
                row_min = val

        # Row-level pruning: if no cell in this row is <= limit, distance cannot be <= limit
        if row_min > k:
            return infinity

        row_prev2 = row_prev
        row_prev = row_curr

    res = row_curr[m]
    return res if res <= k else infinity


def osa_distance(s1: str, s2: str, limit: int | None = None) -> int:
    """Compute Optimal String Alignment distance between s1 and s2.

    If limit is None, computes exact unbounded OSA distance.
    If limit is an integer, returns thresholded distance (at most limit + 1).
    """
    if limit is None:
        return osa_distance_reference(s1, s2)
    return thresholded_osa_distance(s1, s2, limit)

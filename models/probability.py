"""Win probability calculations."""


def log5(rating_a: float, rating_b: float) -> float:
    """Compute P(A beats B) using the Log5 method.

    Args:
        rating_a: Team A's power rating (0-1), e.g. Barthag
        rating_b: Team B's power rating (0-1)

    Returns:
        Probability that A beats B (0-1)
    """
    if rating_a + rating_b == 0:
        return 0.5
    num = rating_a * (1 - rating_b)
    den = rating_a * (1 - rating_b) + rating_b * (1 - rating_a)
    if den == 0:
        return 0.5
    return num / den

"""Team data model."""

from dataclasses import dataclass, field


@dataclass
class Team:
    name: str
    seed: int
    region: str
    rating: float  # Barthag or composite power rating (0-1 scale)
    adj_offense: float = 0.0  # Adjusted offensive efficiency
    adj_defense: float = 0.0  # Adjusted defensive efficiency
    # Public pick percentages by round: {round_num: fraction}
    pick_pcts: dict[int, float] = field(default_factory=dict)

    def __str__(self):
        return f"({self.seed}) {self.name}"

    def __hash__(self):
        return hash((self.name, self.seed, self.region))

    def __eq__(self, other):
        if not isinstance(other, Team):
            return False
        return self.name == other.name and self.seed == other.seed and self.region == other.region

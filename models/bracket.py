"""Bracket data structure.

The bracket is a binary tree stored as a 127-element array (index 0 unused).
- Indices 64-127: the 64 team starting slots (leaves)
- Indices 1-63: game slots (internal nodes)
- Index 1: championship game
- Index 2-3: semifinal games (Final Four)
- Index 4-7: regional finals (Elite Eight)
- ...and so on

Parent of node i is i // 2. Children of node i are 2*i and 2*i+1.

Regions are mapped to the four quarter-brackets:
- Region 0 (e.g. East):    subtree rooted at index 4  -> teams at indices 64-79
- Region 1 (e.g. West):    subtree rooted at index 5  -> teams at indices 80-95
- Region 2 (e.g. South):   subtree rooted at index 6  -> teams at indices 96-111
- Region 3 (e.g. Midwest):  subtree rooted at index 7  -> teams at indices 112-127

Within each region, the 16 teams are placed by standard seed matchup order:
Slot 0: seed 1, Slot 1: seed 16, Slot 2: seed 8, Slot 3: seed 9,
Slot 4: seed 5, Slot 5: seed 12, Slot 6: seed 4, Slot 7: seed 13,
Slot 8: seed 6, Slot 9: seed 11, Slot 10: seed 3, Slot 11: seed 14,
Slot 12: seed 7, Slot 13: seed 10, Slot 14: seed 2, Slot 15: seed 15
"""

from __future__ import annotations

from models.team import Team

# Seeds placed in bracket order within a region (matches standard bracket layout)
SEED_ORDER = [1, 16, 8, 9, 5, 12, 4, 13, 6, 11, 3, 14, 7, 10, 2, 15]


class Bracket:
    """A 64-team tournament bracket."""

    def __init__(self):
        # slots[0] is unused. slots[1..63] are game results. slots[64..127] are starting teams.
        self.slots: list[Team | None] = [None] * 128
        self.regions: dict[int, str] = {}  # region_index (0-3) -> region name
        self.teams: list[Team] = []  # all 64 teams

    def set_team(self, region_index: int, seed_position: int, team: Team):
        """Place a team into its starting slot.

        Args:
            region_index: 0-3
            seed_position: 0-15 (index into SEED_ORDER)
        """
        base = 64 + region_index * 16
        slot = base + seed_position
        self.slots[slot] = team
        if team not in self.teams:
            self.teams.append(team)

    def set_teams_for_region(self, region_index: int, region_name: str, teams_by_seed: dict[int, Team]):
        """Place all 16 teams for a region.

        Args:
            region_index: 0-3
            region_name: e.g. "East"
            teams_by_seed: {seed_number: Team} for seeds 1-16
        """
        self.regions[region_index] = region_name
        for pos, seed in enumerate(SEED_ORDER):
            if seed in teams_by_seed:
                self.set_team(region_index, pos, teams_by_seed[seed])

    def get_matchup(self, game_slot: int) -> tuple[int, int]:
        """Get the two child slot indices that feed into this game."""
        return 2 * game_slot, 2 * game_slot + 1

    def get_team_at(self, slot: int) -> Team | None:
        """Get the team at a slot (either a starting slot or a game winner)."""
        return self.slots[slot]

    def set_winner(self, game_slot: int, team: Team):
        """Set the winner of a game slot."""
        self.slots[game_slot] = team

    def get_round(self, game_slot: int) -> int:
        """Get the round number (1-6) for a game slot.

        Round 6 = championship (slot 1)
        Round 5 = Final Four (slots 2-3)
        Round 4 = Elite Eight (slots 4-7)
        Round 3 = Sweet 16 (slots 8-15)
        Round 2 = Round of 32 (slots 16-31)
        Round 1 = Round of 64 (slots 32-63)
        """
        if game_slot < 1 or game_slot > 63:
            raise ValueError(f"Invalid game slot: {game_slot}")
        r = 0
        s = game_slot
        while s >= 1:
            r += 1
            s //= 2
        # r is now the depth from root +1. Invert: round = 7 - depth
        return 7 - r

    def get_region_index(self, slot: int) -> int | None:
        """Get which region (0-3) a slot belongs to. None for Final Four / Championship."""
        if slot <= 3:
            return None  # Final Four or Championship
        while slot >= 8:
            slot //= 2
        # slot is now 4, 5, 6, or 7
        return slot - 4

    def get_all_game_slots_for_round(self, round_num: int) -> list[int]:
        """Get all game slot indices for a given round."""
        if round_num == 6:
            return [1]
        if round_num == 5:
            return [2, 3]
        if round_num == 4:
            return [4, 5, 6, 7]
        if round_num == 3:
            return list(range(8, 16))
        if round_num == 2:
            return list(range(16, 32))
        if round_num == 1:
            return list(range(32, 64))
        return []

    def get_path_to_championship(self, starting_slot: int) -> list[int]:
        """Get the game slots a team must win to become champion.

        Args:
            starting_slot: The team's starting slot (64-127)

        Returns:
            List of game slot indices from first game to championship [32+, 16+, 8+, 4+, 2-3, 1]
        """
        path = []
        slot = starting_slot // 2
        while slot >= 1:
            path.append(slot)
            slot //= 2
        return path

    def get_starting_slot(self, team: Team) -> int | None:
        """Find the starting slot (64-127) for a team."""
        for i in range(64, 128):
            if self.slots[i] == team:
                return i
        return None

    def get_opponent_slot(self, game_slot: int, team_slot: int) -> int:
        """Given a game slot and one team's incoming slot, get the opponent's incoming slot."""
        left, right = self.get_matchup(game_slot)
        return right if team_slot == left else left

    def get_teams_in_subtree(self, slot: int) -> list[Team]:
        """Get all teams that could potentially reach this game slot."""
        if slot >= 64:
            team = self.slots[slot]
            return [team] if team else []
        left, right = self.get_matchup(slot)
        return self.get_teams_in_subtree(left) + self.get_teams_in_subtree(right)

    def copy(self) -> Bracket:
        """Create a deep copy of this bracket."""
        new = Bracket()
        new.slots = list(self.slots)
        new.regions = dict(self.regions)
        new.teams = list(self.teams)
        return new

    def is_complete(self) -> bool:
        """Check if all 63 game slots have been filled."""
        return all(self.slots[i] is not None for i in range(1, 64))

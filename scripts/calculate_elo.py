#!/usr/bin/env python3
"""
ELO Rating Calculator for AgentBeats Werewolves Leaderboard

This script:
1. Processes game results and calculates proper ELO ratings
2. Writes elo_delta AND elo_after to result JSONs
3. Generates index files for frontend queries:
   - indexes/games.json: List of all games
   - indexes/leaderboard.json: Current rankings
   - indexes/agents/<agent_id>.json: Per-agent game history
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

# Constants
K_FACTOR = 32
INITIAL_ELO = 1000
RESULTS_DIR = Path("results")
INDEXES_DIR = Path("indexes")
AGENTS_DIR = INDEXES_DIR / "agents"
STATE_FILE = Path("elo_state.json")


def ensure_dirs():
    """Create necessary directories."""
    INDEXES_DIR.mkdir(exist_ok=True)
    AGENTS_DIR.mkdir(exist_ok=True)


def load_elo_state() -> Dict[str, Any]:
    """Load current ELO state from file, or create initial state."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {
        "last_updated": None,
        "agents": {},
        "processed_games": []
    }


def save_elo_state(state: Dict[str, Any]) -> None:
    """Save ELO state to file."""
    state["last_updated"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_agent_elo(state: Dict[str, Any], agent_id: str, elo_type: str = "general_elo") -> float:
    """Get agent's current ELO, defaulting to INITIAL_ELO if not found."""
    if agent_id in state["agents"]:
        return state["agents"][agent_id].get(elo_type, INITIAL_ELO)
    return INITIAL_ELO


def calculate_expected_score(player_elo: float, opponent_elo: float) -> float:
    """Calculate expected score using Elo formula."""
    return 1.0 / (1.0 + 10 ** ((opponent_elo - player_elo) / 400))


def calculate_elo_delta(player_elo: float, opponents_avg_elo: float, won: bool) -> float:
    """Calculate ELO change based on opponent strength."""
    expected = calculate_expected_score(player_elo, opponents_avg_elo)
    actual = 1.0 if won else 0.0
    return round(K_FACTOR * (actual - expected), 1)


def get_participants_from_game(game_data: Dict[str, Any]) -> Dict[str, str]:
    """Extract participant mapping (Player_X -> agent_id) from game data."""
    return game_data.get("participants", {})


def get_opponents_avg_elo(
    state: Dict[str, Any],
    participants: Dict[str, str],
    player_name: str,
    player_team: str,
    scores: List[Dict[str, Any]],
    elo_type: str = "general_elo"
) -> float:
    """Calculate average ELO of opponents (other team)."""
    opponent_elos = []

    for score in scores:
        other_player = score["player_name"]
        if other_player == player_name:
            continue

        other_team = score.get("team", "")
        if other_team != player_team:
            other_agent_id = participants.get(other_player, "")
            if other_agent_id:
                opponent_elos.append(get_agent_elo(state, other_agent_id, elo_type))

    if not opponent_elos:
        return INITIAL_ELO

    return sum(opponent_elos) / len(opponent_elos)


def get_game_timestamps(game_data: Dict[str, Any]) -> tuple:
    """Extract start and end timestamps from game action_log."""
    start_ts = None
    end_ts = None

    for result in game_data.get("results", []):
        action_log = result.get("action_log", [])
        for action in action_log:
            ts = action.get("timestamp")
            if ts:
                if start_ts is None or ts < start_ts:
                    start_ts = ts
                if end_ts is None or ts > end_ts:
                    end_ts = ts

    return start_ts, end_ts


def init_agent_state(state: Dict[str, Any], agent_id: str) -> None:
    """Initialize agent in state if not exists."""
    if agent_id not in state["agents"]:
        state["agents"][agent_id] = {
            "general_elo": INITIAL_ELO,
            "werewolf_elo": INITIAL_ELO,
            "villager_elo": INITIAL_ELO,
            "games_played": 0,
            "wins": 0,
            "losses": 0,
            "games_as_werewolf": 0,
            "games_as_villager": 0,
            "wins_as_werewolf": 0,
            "wins_as_villager": 0
        }


def process_game(state: Dict[str, Any], game_file: Path, agent_histories: Dict[str, List]) -> bool:
    """
    Process a single game file and update ELO deltas.
    Returns True if the file was modified.
    """
    with open(game_file, "r") as f:
        game_data = json.load(f)

    filename = game_file.name

    # Skip if already processed
    if filename in state["processed_games"]:
        print(f"  Skipping {filename} (already processed)")
        return False

    participants = get_participants_from_game(game_data)

    # Validate 8 participants
    if len(participants) != 8:
        print(f"  Skipping {filename} (has {len(participants)} participants, need 8)")
        return False

    # Find the results with scores
    results = game_data.get("results", [])
    scores = None
    winner = None
    for result in results:
        if "scores" in result:
            scores = result["scores"]
            winner = result.get("winner")
            break

    if not scores:
        print(f"  Skipping {filename} (no scores found)")
        return False

    print(f"  Processing {filename}...")

    # Get timestamps
    start_ts, end_ts = get_game_timestamps(game_data)

    modified = False

    # Calculate ELO delta for each player
    for score in scores:
        player_name = score["player_name"]
        agent_id = participants.get(player_name, "")
        won = score.get("won", False)
        team = score.get("team", "")
        role = score.get("role", "")

        if not agent_id:
            continue

        # Initialize agent if needed
        init_agent_state(state, agent_id)

        # Get current ELO BEFORE this game
        elo_before = get_agent_elo(state, agent_id, "general_elo")

        # Calculate opponent average ELO
        opponents_avg = get_opponents_avg_elo(
            state, participants, player_name, team, scores, "general_elo"
        )

        # Calculate delta
        elo_delta = calculate_elo_delta(elo_before, opponents_avg, won)

        # Calculate ELO after
        elo_after = round(elo_before + elo_delta, 1)

        # Update score with elo_delta and elo_after
        if score.get("elo_delta") != elo_delta or score.get("elo_after") != elo_after:
            score["elo_delta"] = elo_delta
            score["elo_after"] = elo_after
            modified = True

        # Update agent state
        state["agents"][agent_id]["general_elo"] = elo_after
        state["agents"][agent_id]["games_played"] += 1

        if won:
            state["agents"][agent_id]["wins"] += 1
        else:
            state["agents"][agent_id]["losses"] += 1

        # Update role-specific stats
        if team == "werewolves":
            state["agents"][agent_id]["games_as_werewolf"] += 1
            if won:
                state["agents"][agent_id]["wins_as_werewolf"] += 1

            wolf_elo_before = get_agent_elo(state, agent_id, "werewolf_elo")
            wolf_delta = calculate_elo_delta(wolf_elo_before, opponents_avg, won)
            state["agents"][agent_id]["werewolf_elo"] = round(wolf_elo_before + wolf_delta, 1)
        else:
            state["agents"][agent_id]["games_as_villager"] += 1
            if won:
                state["agents"][agent_id]["wins_as_villager"] += 1

            villager_elo_before = get_agent_elo(state, agent_id, "villager_elo")
            villager_delta = calculate_elo_delta(villager_elo_before, opponents_avg, won)
            state["agents"][agent_id]["villager_elo"] = round(villager_elo_before + villager_delta, 1)

        # Add to agent history
        if agent_id not in agent_histories:
            agent_histories[agent_id] = []

        agent_histories[agent_id].append({
            "game": filename,
            "player_name": player_name,
            "role": role,
            "team": team,
            "won": won,
            "elo_before": elo_before,
            "elo_delta": elo_delta,
            "elo_after": elo_after,
            "aggregate_score": round(score.get("metrics", {}).get("aggregate_score", 0) * 100, 1),
            "start_time": start_ts,
            "end_time": end_ts
        })

        print(f"    {player_name} ({agent_id[:8]}...): {elo_before:.0f} → {elo_after:.0f} "
              f"({'+' if elo_delta >= 0 else ''}{elo_delta})")

    # Save updated game data
    if modified:
        with open(game_file, "w") as f:
            json.dump(game_data, f, indent=2)

    # Mark as processed
    state["processed_games"].append(filename)

    return modified


def generate_games_index(state: Dict[str, Any]) -> None:
    """Generate indexes/games.json with list of all processed games."""
    games = []

    for filename in sorted(state["processed_games"]):
        game_file = RESULTS_DIR / filename
        if not game_file.exists():
            continue

        with open(game_file, "r") as f:
            game_data = json.load(f)

        participants = game_data.get("participants", {})
        unique_agents = list(set(participants.values()))

        start_ts, end_ts = get_game_timestamps(game_data)

        # Get winner
        winner = None
        for result in game_data.get("results", []):
            if "winner" in result:
                winner = result["winner"]
                break

        games.append({
            "filename": filename,
            "start_time": start_ts,
            "end_time": end_ts,
            "participants": unique_agents,
            "participant_count": len(participants),
            "winner": winner,
            "traceability_url": f"https://agentbeats.dev/{filename.replace('.json', '')}"
        })

    # Sort by end_time descending
    games.sort(key=lambda x: x.get("end_time") or "", reverse=True)

    index = {
        "last_updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_games": len(games),
        "games": games
    }

    with open(INDEXES_DIR / "games.json", "w") as f:
        json.dump(index, f, indent=2)

    print(f"Generated indexes/games.json ({len(games)} games)")


def generate_leaderboard_index(state: Dict[str, Any]) -> None:
    """Generate indexes/leaderboard.json with current rankings."""
    rankings = []

    for agent_id, data in state["agents"].items():
        games_played = data.get("games_played", 0)
        if games_played == 0:
            continue

        wins = data.get("wins", 0)
        win_rate = round(wins * 100.0 / games_played, 1) if games_played > 0 else 0

        rankings.append({
            "agent_id": agent_id,
            "general_elo": data.get("general_elo", INITIAL_ELO),
            "werewolf_elo": data.get("werewolf_elo", INITIAL_ELO),
            "villager_elo": data.get("villager_elo", INITIAL_ELO),
            "games_played": games_played,
            "wins": wins,
            "losses": data.get("losses", 0),
            "win_rate": win_rate,
            "games_as_werewolf": data.get("games_as_werewolf", 0),
            "games_as_villager": data.get("games_as_villager", 0),
            "wins_as_werewolf": data.get("wins_as_werewolf", 0),
            "wins_as_villager": data.get("wins_as_villager", 0)
        })

    # Sort by general_elo descending
    rankings.sort(key=lambda x: x["general_elo"], reverse=True)

    # Add rank
    for i, r in enumerate(rankings):
        r["rank"] = i + 1

    leaderboard = {
        "last_updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_agents": len(rankings),
        "rankings": rankings
    }

    with open(INDEXES_DIR / "leaderboard.json", "w") as f:
        json.dump(leaderboard, f, indent=2)

    print(f"Generated indexes/leaderboard.json ({len(rankings)} agents)")


def generate_agent_indexes(state: Dict[str, Any], agent_histories: Dict[str, List]) -> None:
    """Generate indexes/agents/<agent_id>.json for each agent."""
    for agent_id, history in agent_histories.items():
        agent_data = state["agents"].get(agent_id, {})

        # Sort history by end_time descending
        history.sort(key=lambda x: x.get("end_time") or "", reverse=True)

        agent_index = {
            "agent_id": agent_id,
            "last_updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "current_elo": {
                "general": agent_data.get("general_elo", INITIAL_ELO),
                "werewolf": agent_data.get("werewolf_elo", INITIAL_ELO),
                "villager": agent_data.get("villager_elo", INITIAL_ELO)
            },
            "stats": {
                "games_played": agent_data.get("games_played", 0),
                "wins": agent_data.get("wins", 0),
                "losses": agent_data.get("losses", 0),
                "games_as_werewolf": agent_data.get("games_as_werewolf", 0),
                "games_as_villager": agent_data.get("games_as_villager", 0),
                "wins_as_werewolf": agent_data.get("wins_as_werewolf", 0),
                "wins_as_villager": agent_data.get("wins_as_villager", 0)
            },
            "game_history": history
        }

        # Use safe filename (replace special chars)
        safe_id = agent_id.replace("/", "_").replace("\\", "_")
        agent_file = AGENTS_DIR / f"{safe_id}.json"

        with open(agent_file, "w") as f:
            json.dump(agent_index, f, indent=2)

    print(f"Generated indexes/agents/*.json ({len(agent_histories)} agents)")


def load_existing_agent_histories(state: Dict[str, Any]) -> Dict[str, List]:
    """Load existing agent histories from index files."""
    histories = {}

    if not AGENTS_DIR.exists():
        return histories

    for agent_file in AGENTS_DIR.glob("*.json"):
        try:
            with open(agent_file, "r") as f:
                data = json.load(f)
            agent_id = data.get("agent_id")
            if agent_id:
                histories[agent_id] = data.get("game_history", [])
        except Exception as e:
            print(f"  Warning: Could not load {agent_file}: {e}")

    return histories


def main():
    print("=" * 60)
    print("ELO Rating Calculator for AgentBeats Werewolves")
    print("=" * 60)
    print()

    # Ensure directories exist
    ensure_dirs()

    # Load state
    state = load_elo_state()
    print(f"Loaded state: {len(state['agents'])} agents, "
          f"{len(state['processed_games'])} games processed")
    print()

    # Load existing agent histories
    agent_histories = load_existing_agent_histories(state)

    # Find all result files
    if not RESULTS_DIR.exists():
        print("No results directory found!")
        return

    result_files = sorted(RESULTS_DIR.glob("*.json"))
    print(f"Found {len(result_files)} result files")
    print()

    # Process each game
    files_modified = 0
    games_processed = 0

    for game_file in result_files:
        if game_file.name in state["processed_games"]:
            continue

        if process_game(state, game_file, agent_histories):
            files_modified += 1
        games_processed += 1

    print()
    print("=" * 60)
    print(f"Summary: {games_processed} new games, {files_modified} files modified")
    print("=" * 60)
    print()

    # Save state
    save_elo_state(state)
    print(f"State saved to {STATE_FILE}")

    # Generate index files
    print()
    print("Generating index files...")
    generate_games_index(state)
    generate_leaderboard_index(state)
    generate_agent_indexes(state, agent_histories)

    # Print current rankings
    if state["agents"]:
        print()
        print("Current ELO Rankings:")
        print("-" * 60)
        sorted_agents = sorted(
            state["agents"].items(),
            key=lambda x: x[1].get("general_elo", INITIAL_ELO),
            reverse=True
        )
        for i, (agent_id, data) in enumerate(sorted_agents, 1):
            print(f"  {i}. {agent_id[:20]}... : {data.get('general_elo', INITIAL_ELO):.0f} ELO "
                  f"({data.get('games_played', 0)} games, "
                  f"{data.get('wins', 0)}W/{data.get('losses', 0)}L)")


if __name__ == "__main__":
    main()

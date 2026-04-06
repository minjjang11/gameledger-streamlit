import streamlit as st
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

DB_PATH = "gameledger.db"


# -----------------------------
# Utility functions
# -----------------------------
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_address_like_id() -> str:
    return "0x" + secrets.token_hex(20)


def generate_secret_code() -> str:
    return f"{secrets.randbelow(9000) + 1000}"


def generate_match_id() -> str:
    return secrets.token_hex(4).upper()


def payoff(choice1: str, choice2: str) -> Tuple[int, int]:
    matrix = {
        ("Cooperate", "Cooperate"): (3, 3),
        ("Cooperate", "Defect"): (0, 5),
        ("Defect", "Cooperate"): (5, 0),
        ("Defect", "Defect"): (1, 1),
    }
    return matrix[(choice1, choice2)]


# -----------------------------
# Database setup
# -----------------------------
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS participants (
            address TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            secret_code TEXT,
            games_played INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blocks (
            idx INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            data_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            hash TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS matches (
            match_id TEXT PRIMARY KEY,
            player1_address TEXT NOT NULL,
            player2_address TEXT NOT NULL,
            choice1 TEXT,
            choice2 TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """
    )

    cur.execute("SELECT COUNT(*) FROM blocks")
    block_count = cur.fetchone()[0]
    if block_count == 0:
        genesis_data = {
            "type": "genesis",
            "message": "GameLedger Genesis Block"
        }
        timestamp = datetime.utcnow().isoformat()
        block_dict = {
            "index": 0,
            "timestamp": timestamp,
            "data": genesis_data,
            "previous_hash": "0",
        }
        block_hash = sha256_text(json.dumps(block_dict, sort_keys=True))
        cur.execute(
            "INSERT INTO blocks (idx, timestamp, data_json, previous_hash, hash) VALUES (?, ?, ?, ?, ?)",
            (0, timestamp, json.dumps(genesis_data, sort_keys=True), "0", block_hash),
        )

    conn.commit()
    conn.close()


# -----------------------------
# Blockchain persistence helpers
# -----------------------------
def calculate_block_hash(index: int, timestamp: str, data: Dict, previous_hash: str) -> str:
    block_string = json.dumps(
        {
            "index": index,
            "timestamp": timestamp,
            "data": data,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
    )
    return sha256_text(block_string)


def fetch_blocks() -> List[Dict]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM blocks ORDER BY idx ASC")
    rows = cur.fetchall()
    conn.close()

    blocks = []
    for row in rows:
        blocks.append(
            {
                "index": row["idx"],
                "timestamp": row["timestamp"],
                "data": json.loads(row["data_json"]),
                "previous_hash": row["previous_hash"],
                "hash": row["hash"],
            }
        )
    return blocks


def add_block(data: Dict) -> Dict:
    blocks = fetch_blocks()
    latest = blocks[-1]
    new_index = latest["index"] + 1
    timestamp = datetime.utcnow().isoformat()
    previous_hash = latest["hash"]
    block_hash = calculate_block_hash(new_index, timestamp, data, previous_hash)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO blocks (idx, timestamp, data_json, previous_hash, hash) VALUES (?, ?, ?, ?, ?)",
        (new_index, timestamp, json.dumps(data, sort_keys=True), previous_hash, block_hash),
    )
    conn.commit()
    conn.close()

    return {
        "index": new_index,
        "timestamp": timestamp,
        "data": data,
        "previous_hash": previous_hash,
        "hash": block_hash,
    }


def is_chain_valid() -> Tuple[bool, str]:
    blocks = fetch_blocks()
    for i in range(1, len(blocks)):
        current = blocks[i]
        previous = blocks[i - 1]

        recalculated_hash = calculate_block_hash(
            current["index"],
            current["timestamp"],
            current["data"],
            current["previous_hash"],
        )

        if current["hash"] != recalculated_hash:
            return False, f"Block {current['index']} hash mismatch."

        if current["previous_hash"] != previous["hash"]:
            return False, f"Block {current['index']} previous_hash mismatch."

    return True, "Chain is valid."


def tamper_block(block_index: int, field_name: str, new_value: str) -> bool:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT data_json FROM blocks WHERE idx = ?", (block_index,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        return False

    data = json.loads(row["data_json"])
    if field_name not in data:
        conn.close()
        return False

    data[field_name] = new_value
    cur.execute("UPDATE blocks SET data_json = ? WHERE idx = ?", (json.dumps(data, sort_keys=True), block_index))
    conn.commit()
    conn.close()
    return True


# -----------------------------
# Participant helpers
# -----------------------------
def create_participant(username: str, create_secret: bool) -> Tuple[str, Optional[str]]:
    address = generate_address_like_id()
    secret_code = generate_secret_code() if create_secret else None
    created_at = datetime.utcnow().isoformat()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO participants (address, username, secret_code, games_played, created_at) VALUES (?, ?, ?, 0, ?)",
        (address, username.strip(), secret_code, created_at),
    )
    conn.commit()
    conn.close()

    return address, secret_code


def fetch_participants() -> List[Dict]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM participants ORDER BY created_at ASC")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_participant_map() -> Dict[str, Dict]:
    participants = fetch_participants()
    return {p["address"]: p for p in participants}


def update_games_played(player1: str, player2: str):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("UPDATE participants SET games_played = games_played + 1 WHERE address = ?", (player1,))
    cur.execute("UPDATE participants SET games_played = games_played + 1 WHERE address = ?", (player2,))

    conn.commit()
    conn.close()


def fetch_history_for_address(address: str) -> List[Dict]:
    blocks = fetch_blocks()
    history = []
    for block in blocks[1:]:
        data = block["data"]
        if data.get("player1_address") == address or data.get("player2_address") == address:
            history.append(block)
    return history


def compute_participant_stats(address: str) -> Dict:
    history = fetch_history_for_address(address)
    games_played = len(history)
    cooperations = 0
    defections = 0
    wins = 0
    draws = 0
    losses = 0
    total_payoff = 0

    for block in history:
        data = block["data"]
        is_player1 = data.get("player1_address") == address

        if is_player1:
            choice = data.get("choice1")
            payoff_value = data.get("payoff1", 0)
        else:
            choice = data.get("choice2")
            payoff_value = data.get("payoff2", 0)

        total_payoff += payoff_value

        if choice == "Cooperate":
            cooperations += 1
        elif choice == "Defect":
            defections += 1

        winner = data.get("winner")
        if winner == "Draw":
            draws += 1
        elif winner == address:
            wins += 1
        else:
            losses += 1

    average_payoff = round(total_payoff / games_played, 2) if games_played > 0 else 0
    cooperation_rate = round((cooperations / games_played) * 100, 1) if games_played > 0 else 0

    return {
        "games_played": games_played,
        "cooperations": cooperations,
        "defections": defections,
        "average_payoff": average_payoff,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "cooperation_rate": cooperation_rate,
    }


# -----------------------------
# Match helpers
# -----------------------------
def create_match(player1_address: str, player2_address: str) -> str:
    match_id = generate_match_id()
    created_at = datetime.utcnow().isoformat()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO matches (match_id, player1_address, player2_address, choice1, choice2, status, created_at, resolved_at) VALUES (?, ?, ?, NULL, NULL, ?, ?, NULL)",
        (match_id, player1_address, player2_address, "pending", created_at),
    )
    conn.commit()
    conn.close()
    return match_id


def fetch_matches() -> List[Dict]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM matches ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_match(match_id: str) -> Optional[Dict]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def resolve_match_if_ready(match_id: str):
    match = get_match(match_id)
    if not match:
        return None
    if not match["choice1"] or not match["choice2"] or match["status"] == "resolved":
        return None

    participant_map = get_participant_map()
    player1 = match["player1_address"]
    player2 = match["player2_address"]
    choice1 = match["choice1"]
    choice2 = match["choice2"]
    payoff1, payoff2 = payoff(choice1, choice2)

    if payoff1 > payoff2:
        winner = player1
    elif payoff2 > payoff1:
        winner = player2
    else:
        winner = None

    result_data = {
        "game": "PrisonersDilemma",
        "match_id": match_id,
        "player1_username": participant_map[player1]["username"],
        "player1_address": player1,
        "player2_username": participant_map[player2]["username"],
        "player2_address": player2,
        "choice1": choice1,
        "choice2": choice2,
        "payoff1": payoff1,
        "payoff2": payoff2,
        "winner": winner if winner else "Draw",
    }

    result_hash = sha256_text(json.dumps(result_data, sort_keys=True))
    result_data["result_hash"] = result_hash
    add_block(result_data)
    update_games_played(player1, player2)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE matches SET status = ?, resolved_at = ? WHERE match_id = ?",
        ("resolved", datetime.utcnow().isoformat(), match_id),
    )
    conn.commit()
    conn.close()

    return result_data


def submit_choice(match_id: str, player_address: str, choice: str) -> Tuple[bool, str]:
    match = get_match(match_id)
    if not match:
        return False, "Match not found."
    if match["status"] == "resolved":
        return False, "This match has already been resolved."

    conn = get_conn()
    cur = conn.cursor()

    if player_address == match["player1_address"]:
        if match["choice1"]:
            conn.close()
            return False, "Player 1 has already submitted a choice."
        cur.execute("UPDATE matches SET choice1 = ? WHERE match_id = ?", (choice, match_id))
    elif player_address == match["player2_address"]:
        if match["choice2"]:
            conn.close()
            return False, "Player 2 has already submitted a choice."
        cur.execute("UPDATE matches SET choice2 = ? WHERE match_id = ?", (choice, match_id))
    else:
        conn.close()
        return False, "This address is not part of the selected match."

    conn.commit()
    conn.close()

    resolved_data = resolve_match_if_ready(match_id)
    if resolved_data:
        return True, "Choice submitted. Both players have now submitted, so the match was resolved and recorded on the chain."

    return True, "Choice submitted. Waiting for the other player's decision."


# -----------------------------
# App start
# -----------------------------
init_db()
st.set_page_config(page_title="GameLedger", page_icon="⛓️", layout="centered")

st.title("⛓️ GameLedger")
st.caption("A blockchain-style Prisoner's Dilemma record system")

with st.expander("Project summary", expanded=False):
    st.write(
        "This demo lets users play Prisoner's Dilemma, stores each game outcome as a block record, "
        "and validates whether the stored chain has been altered afterwards."
    )

participants = fetch_participants()
participant_map = get_participant_map()
participant_addresses = [p["address"] for p in participants]

# -----------------------------
# 1. Participant registration
# -----------------------------
st.header("1) Participant registration")
with st.form("register_form"):
    username = st.text_input("Enter a username")
    create_secret = st.checkbox("Generate secret code as well", value=True)
    submitted = st.form_submit_button("Create participant identity")

if submitted:
    if not username.strip():
        st.error("Please enter a username.")
    else:
        address, secret_code = create_participant(username, create_secret)
        st.success("Participant created.")
        st.write(f"**Username:** {username.strip()}")
        st.write(f"**Address-like ID:** `{address}`")
        if secret_code:
            st.write(f"**Secret code:** `{secret_code}`")
            st.info("Save this if you want a lightweight identity check later.")

participants = fetch_participants()
participant_map = get_participant_map()
participant_addresses = [p["address"] for p in participants]

if participants:
    st.subheader("Current participants")
    participant_rows = []
    for info in participants:
        stats = compute_participant_stats(info["address"])
        participant_rows.append(
            {
                "username": info["username"],
                "address": info["address"],
                "games_played": stats["games_played"],
                "cooperations": stats["cooperations"],
                "defections": stats["defections"],
                "average_payoff": stats["average_payoff"],
                "wins": stats["wins"],
                "draws": stats["draws"],
                "losses": stats["losses"],
                "cooperation_rate_%": stats["cooperation_rate"],
            }
        )
    st.dataframe(participant_rows, use_container_width=True)


# -----------------------------
# 2. Create a match
# -----------------------------
st.header("2) Create a match")
if len(participant_addresses) < 2:
    st.warning("Create at least two participants before creating a match.")
else:
    with st.form("match_form"):
        match_player1 = st.selectbox("Player 1 address", participant_addresses, key="match_p1")
        match_player2_options = [p for p in participant_addresses if p != match_player1]
        match_player2 = st.selectbox("Player 2 address", match_player2_options, key="match_p2")
        submitted_match = st.form_submit_button("Create match")

    if submitted_match:
        match_id = create_match(match_player1, match_player2)
        st.success("Match created.")
        st.write(f"**Match ID:** `{match_id}`")
        st.info("Share this Match ID with both players. Each player should submit their choice from their own device.")


# -----------------------------
# 3. Submit a private choice
# -----------------------------
st.header("3) Submit a private choice")
if not participant_addresses:
    st.info("No participants yet.")
else:
    with st.form("submit_choice_form"):
        submit_address = st.selectbox("Your address", participant_addresses, key="submit_addr")
        submit_match_id = st.text_input("Match ID").strip().upper()
        submit_choice_value = st.radio("Your choice", ["Cooperate", "Defect"], horizontal=True)
        submit_choice_btn = st.form_submit_button("Submit my private choice")

    if submit_choice_btn:
        if not submit_match_id:
            st.error("Please enter a Match ID.")
        else:
            ok, message = submit_choice(submit_match_id, submit_address, submit_choice_value)
            if ok:
                st.success(message)
            else:
                st.error(message)


# -----------------------------
# 4. Match status and results
# -----------------------------
st.header("4) Match status")
matches = fetch_matches()
if not matches:
    st.info("No matches yet.")
else:
    match_rows = []
    for m in matches:
        match_rows.append(
            {
                "match_id": m["match_id"],
                "player1_address": m["player1_address"],
                "player2_address": m["player2_address"],
                "choice1_submitted": bool(m["choice1"]),
                "choice2_submitted": bool(m["choice2"]),
                "status": m["status"],
            }
        )
    st.dataframe(match_rows, use_container_width=True)
    st.caption("Choices remain hidden until both players have submitted.")


# -----------------------------
# 5. Validate chain
# -----------------------------
st.header("5) Validate chain")
if st.button("Validate blockchain"):
    valid, message = is_chain_valid()
    if valid:
        st.success(message)
    else:
        st.error(message)


# -----------------------------
# 6. Tampering demo
# -----------------------------
st.header("6) Tampering demo")
st.write("Change a stored value to demonstrate why validation matters.")

blocks = fetch_blocks()
chain_length = len(blocks)
if chain_length <= 1:
    st.info("Resolve at least one match first.")
else:
    editable_indices = [b["index"] for b in blocks[1:]]
    selected_block = st.selectbox("Choose block to tamper with", editable_indices)
    selected_block_data = next(b for b in blocks if b["index"] == selected_block)["data"]
    selected_data_keys = list(selected_block_data.keys())
    selected_key = st.selectbox("Choose field", selected_data_keys)
    new_value = st.text_input("New fake value")

    if st.button("Tamper selected block"):
        changed = tamper_block(selected_block, selected_key, new_value)
        if changed:
            st.warning("Block data changed. Validate the chain again to see the result.")
        else:
            st.error("Could not tamper with the selected block.")


# -----------------------------
# 7. Blockchain records
# -----------------------------
st.header("7) Blockchain records")
blocks = fetch_blocks()
for block in blocks:
    with st.expander(f"Block {block['index']}", expanded=(block["index"] == len(blocks) - 1)):
        st.json(block)


# -----------------------------
# 8. Participant history
# -----------------------------
st.header("8) Participant history")
if not participant_addresses:
    st.info("No participants yet.")
else:
    selected_history_address = st.selectbox("Choose a participant address", participant_addresses, key="history_select")
    selected_stats = compute_participant_stats(selected_history_address)
    st.write({
        "games_played": selected_stats["games_played"],
        "cooperations": selected_stats["cooperations"],
        "defections": selected_stats["defections"],
        "average_payoff": selected_stats["average_payoff"],
        "wins": selected_stats["wins"],
        "draws": selected_stats["draws"],
        "losses": selected_stats["losses"],
        "cooperation_rate_%": selected_stats["cooperation_rate"],
    })
    history = fetch_history_for_address(selected_history_address)
    if not history:
        st.info("This participant has no resolved games yet.")
    else:
        st.write(f"Showing {len(history)} recorded game(s) for `{selected_history_address}`")
        for block in history:
            with st.expander(f"Game block {block['index']}"):
                st.json(block)


# -----------------------------
# 9. Solidity extension placeholder
# -----------------------------
st.header("9) Solidity extension")
latest_hash = blocks[-1]["data"].get("result_hash") if len(blocks) > 1 else None
st.write(
    "Next step: store the latest result hash in a tiny Solidity smart contract, "
    "so the Python record system can be extended with an Ethereum-based proof layer."
)

if latest_hash:
    st.write("Latest result hash to anchor in Solidity:")
    st.code(latest_hash, language="text")
else:
    st.info("No result hash yet. Resolve a match first.")


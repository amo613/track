#!/usr/bin/env python3

import streamlit as st
import logging
import random
import time
import requests
import re
import os
import json
from decimal import Decimal
import pandas as pd
import plotly.express as px
from streamlit_autorefresh import st_autorefresh
from streamlit_lottie import st_lottie

################# LOGGING CONFIG ###################
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("app.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

################# SHYFT & ON-CHAIN CONFIG ##############
SHYFT_API_KEY = "738Ve00HEdkbrj31"
SHYFT_BASE_URL = "https://api.shyft.to/sol/v1/transaction/history"
SOLANA_RPC_ENDPOINT = "https://rpc.shyft.to?api_key=738Ve00HEdkbrj31"

################# PAGE CONFIG + CUSTOM STYLING #########
# Uncomment the next line if you want to set a custom page title and layout
# st.set_page_config(page_title="Full Holding Dashboard", layout="wide")

def add_custom_styling():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
        :root {
            --primary-color: #00695c;
            --secondary-color: #004d40;
            --background-gradient: linear-gradient(to bottom right, #fefefe, #cfd9df);
            --text-color: #2F4F4F;
            --accent-color: #a7ffeb;
        }
        html, body, [class*="css"] {
            font-family: 'Roboto', sans-serif !important;
            color: var(--text-color) !important;
        }
        .main {
            background: var(--background-gradient) !important;
            padding: 2rem;
        }
        h1, h2, h3 {
            color: var(--primary-color) !important;
            font-weight: 700 !important;
            text-align: center !important;
        }
        .stDataFrame table {
            text-align: center !important;
            font-size: 1rem;
        }
        .stSlider > div[data-baseweb="slider"] > div {
            background-color: var(--accent-color) !important;
            border-radius: 10px;
        }
        .stButton button {
            background-color: var(--primary-color);
            color: white;
            border: none;
            border-radius: 8px;
            padding: 10px 20px;
            font-size: 1rem;
            transition: background-color 0.3s ease;
        }
        .stButton button:hover {
            background-color: var(--secondary-color);
        }
        .stProgress > div > div > div {
            background-color: var(--primary-color) !important;
            border-radius: 10px;
        }
        .streamlit-expanderHeader {
            color: var(--primary-color) !important;
            font-weight: 700;
        }
        footer {
            visibility: hidden;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

################# FILES FOR JSON STORAGE ##################
MONITORED_ADDRESSES_FILE = "monitored_addresses.json"
ELIGIBLE_TOKENS_FILE = "eligible_tokens.json"
ALL_MONITORED_TOKENS_FILE = "all_monitored_tokens.json"
KOH_HISTORY_FILE = "king_of_the_hill_history.json"

def load_monitored_addresses():
    """
    Loads monitored addresses from session state.
    Initializes the session state key if it doesn't exist.
    """
    if "monitored_tokens" not in st.session_state:
        st.session_state["monitored_tokens"] = []
    return st.session_state["monitored_tokens"]

def save_monitored_addresses(addresses):
    """
    Saves monitored addresses to session state.
    """
    st.session_state["monitored_tokens"] = addresses

def load_eligible_tokens():
    if os.path.exists(ELIGIBLE_TOKENS_FILE):
        try:
            with open(ELIGIBLE_TOKENS_FILE, "r") as f:
                return json.load(f)
        except:
            logger.error("Failed to load eligible_tokens.json")
            return []
    return []

def save_eligible_tokens(tokens):
    try:
        with open(ELIGIBLE_TOKENS_FILE, "w") as f:
            json.dump(tokens, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save eligible tokens => {e}")

def load_all_monitored_tokens():
    if os.path.exists(ALL_MONITORED_TOKENS_FILE):
        try:
            with open(ALL_MONITORED_TOKENS_FILE, "r") as f:
                return json.load(f)
        except:
            logger.error("Failed to load all_monitored_tokens.json")
            return []
    return []

def save_all_monitored_tokens(tokens):
    try:
        with open(ALL_MONITORED_TOKENS_FILE, "w") as f:
            json.dump(tokens, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save all monitored tokens => {e}")

def load_king_of_the_hill_history():
    if os.path.exists(KOH_HISTORY_FILE):
        try:
            with open(KOH_HISTORY_FILE, "r") as f:
                return json.load(f)
        except:
            logger.error("Failed to load king_of_the_hill_history.json")
            return []
    return []

def save_king_of_the_hill_history(history):
    try:
        with open(KOH_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save KoH history => {e}")

######################## ON-CHAIN / SHYFT HELPERS (Used in Token Analysis) ####
@st.cache_data(ttl=600)
def send_rpc_request(payload, retries=5, backoff_factor=0.5, max_backoff=16):
    """
    Sends an RPC request to the Solana endpoint with built-in retry/backoff.
    """
    for attempt in range(retries):
        try:
            r = requests.post(SOLANA_RPC_ENDPOINT, json=payload)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                # Too Many Requests => backoff
                wait_time = min(backoff_factor * (2**attempt), max_backoff)
                time.sleep(wait_time + random.uniform(0, 0.3))
            else:
                r.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"send_rpc_request => {e}")
            time.sleep(min(backoff_factor * (2**attempt), max_backoff))
    logger.error("Max retries in send_rpc_request.")
    return {}

@st.cache_data(ttl=600)
def get_largest_token_holders_with_total_supply(token_mint, top_n=20):
    """
    Returns a list of the top N largest holders for 'token_mint'
    plus the total supply of that token.
    """
    pl = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [str(token_mint), {"commitment": "finalized"}]
    }
    res = send_rpc_request(pl)
    if not res or "result" not in res:
        logger.error("Failed largest accounts => no data")
        return [], Decimal(0)

    top_vals = res["result"]["value"][:top_n]
    t_acct_addrs = [v["address"] for v in top_vals]
    raw_amounts = [Decimal(v.get("amount", "0")) for v in top_vals]

    # fetch owners => getMultipleAccounts
    batch_p = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "getMultipleAccounts",
        "params": [t_acct_addrs, {"encoding": "jsonParsed", "commitment": "finalized"}]
    }
    batch_r = send_rpc_request(batch_p)
    if not batch_r or "result" not in batch_r:
        owners = ["Unknown"] * len(t_acct_addrs)
    else:
        owners_info = batch_r["result"].get("value", [])
        owners = []
        for ai in owners_info:
            if not ai:
                owners.append("Unknown")
            else:
                parsed_d = ai.get("data", {}).get("parsed", {})
                inf = parsed_d.get("info", {})
                owners.append(inf.get("owner", "Unknown"))

    # total supply => getTokenSupply
    sup_p = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "getTokenSupply",
        "params": [str(token_mint), {"commitment": "finalized"}]
    }
    sup_r = send_rpc_request(sup_p)
    if not sup_r or "result" not in sup_r or "value" not in sup_r["result"]:
        total_sup = Decimal(0)
        decimals = 0
    else:
        val = sup_r["result"]["value"]
        raw_sup = Decimal(val.get("amount", "0"))
        decimals = int(val.get("decimals", 0))
        total_sup = raw_sup / (Decimal(10) ** decimals) if decimals > 0 else raw_sup

    holders = []
    for own, raw_amt in zip(owners, raw_amounts):
        if total_sup > 0:
            adjusted = raw_amt / (Decimal(10) ** decimals)
            pct = (adjusted / total_sup) * 100
        else:
            adjusted = Decimal(0)
            pct = Decimal(0)
        holders.append((own, adjusted, pct))
    return holders, total_sup

def fetch_creator_wallet(mint_address):
    """
    Fetch the creator wallet (developer wallet) for a given mint address.
    """
    if not mint_address or not is_valid_pubkey(mint_address):
        logger.error(f"Invalid mint address passed to fetch_creator_wallet: {mint_address}")
        return None

    url = f"https://frontend-api.pump.fun/coins/{mint_address}?sync=true"
    try:
        response = requests.get(url, headers={'accept': '*/*'}, timeout=10)
        response.raise_for_status()
        data = response.json()
        creator = data.get("creator", "Unknown")
        if creator == "Unknown":
            logger.warning(f"Creator not found for mint: {mint_address}")
        return creator
    except requests.RequestException as e:
        logger.error(f"Failed to fetch creator wallet for {mint_address}: {e}")
        return None




def check_received_from_bonding_curve_via_shyft(holder_addr_str, bonding_curve_str, target_mint_str, tx_num=10):
    """
    Checks inbound token transactions (token_mint_str) from 'bonding_curve_str' to 'holder_addr_str'.
    """
    base_url = SHYFT_BASE_URL
    headers = {"x-api-key": SHYFT_API_KEY}
    params = {
        "network": "mainnet-beta",
        "tx_num": tx_num,
        "account": holder_addr_str,
        "enable_raw": "false"
    }

    total_recv = Decimal(0)
    results = []
    try:
        r = requests.get(base_url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        if data.get("success") and isinstance(data.get("result"), list):
            txs = data["result"]
            for tx_obj in txs:
                sigs = tx_obj.get("signatures", ["NoSig"])
                sig = sigs[0]
                actions = tx_obj.get("actions", [])
                for action in actions:
                    info = action.get("info", {})
                    token_addr = info.get("token_address", "")
                    amt_str = info.get("amount", "0")
                    source = info.get("source", "")
                    if token_addr == target_mint_str and source == bonding_curve_str:
                        results.append((sig, amt_str))
                        total_recv += Decimal(amt_str)
        else:
            logger.error("Shyft => inbound BC => unexpected data struct")
    except requests.exceptions.RequestException as e:
        logger.error(f"check_received_from_bonding_curve_via_shyft => {e}")
    return total_recv, results

def check_outgoing_via_shyft(wallet_addr_str, target_mint_str, tx_num=10):
    """
    Checks outbound token transactions (token_mint_str) from 'wallet_addr_str'.
    """
    base_url = SHYFT_BASE_URL
    headers = {"x-api-key": SHYFT_API_KEY}
    params = {
        "network": "mainnet-beta",
        "tx_num": tx_num,
        "account": wallet_addr_str,
        "enable_raw": "false"
    }

    total_out = Decimal(0)
    outgoing_data = []
    try:
        r = requests.get(base_url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        if data.get("success") and isinstance(data.get("result"), list):
            for tx_obj in data["result"]:
                sigs = tx_obj.get("signatures", ["NoSig"])
                sig = sigs[0]
                for action in tx_obj.get("actions", []):
                    info = action.get("info", {})
                    token_addr = info.get("token_address", "")
                    amt_str = info.get("amount", "0")
                    source = info.get("source", "")
                    if token_addr == target_mint_str and source == wallet_addr_str:
                        outgoing_data.append((sig, amt_str))
                        total_out += Decimal(amt_str)
        else:
            logger.error("Shyft => dev wallet => unexpected data struct")
    except requests.exceptions.RequestException as e:
        logger.error(f"check_outgoing_via_shyft => {e}")
    except Exception as e:
        logger.error(f"Unknown error => check_outgoing_via_shyft => {e}")
    return total_out, outgoing_data

def calculate_velocity(history, current_index=0, previous_index=1):
    """
    Calculates the percentage increase/decrease in 'usd_market_cap' between
    two points in history.
    """
    try:
        current_mc = Decimal(history[current_index].get("usd_market_cap", 0))
        prev_mc = Decimal(history[previous_index].get("usd_market_cap", 0))
        if prev_mc == 0:
            return Decimal(0)
        return ((current_mc - prev_mc) / prev_mc) * 100
    except (IndexError, Decimal.InvalidOperation) as e:
        logger.error(f"Velocity calc => {e}")
        return Decimal(0)

################ MONITORING (Used in "Criteria Monitoring") ############
def get_recent_transactions(address):
    """
    Returns the 10 most recent transactions for 'address' from Shyft.
    """
    base_url = SHYFT_BASE_URL
    headers = {"x-api-key": SHYFT_API_KEY}
    params = {"network": "mainnet-beta", "tx_num": 10, "account": address, "enable_raw": "false"}
    try:
        r = requests.get(base_url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        if data.get("success") and isinstance(data.get("result"), list):
            return data["result"]
        else:
            logger.error(f"get_recent_transactions => unexpected => {data}")
            return []
    except requests.exceptions.RequestException as e:
        logger.error(f"get_recent_transactions => {e}")
        return []
    except Exception as e:
        logger.error(f"Error => get_recent_transactions => {e}")
        return []

def get_liquidity(token_mint):
    """
    Placeholder random approach for liquidity.
    """
    val = random.uniform(30000, 50000)
    return Decimal(val)

def get_market_cap(token_mint):
    """
    Placeholder random approach for market cap.
    """
    val = random.uniform(50000, 150000)
    return Decimal(val)

@st.cache_data(ttl=600)
def get_token_holdings(token_mint, wallet_addr):
    """
    Returns how many tokens 'wallet_addr' holds of 'token_mint'.
    """
    pl = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [wallet_addr, {"mint": token_mint}, {"encoding": "jsonParsed"}]
    }
    resp = send_rpc_request(pl)
    if not resp or "result" not in resp:
        return 0
    total_amt = Decimal(0)
    for acct in resp["result"]["value"]:
        info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        bal = info.get("tokenAmount", {}).get("uiAmount", 0)
        total_amt += Decimal(bal)
    return float(total_amt)

@st.cache_data(ttl=600)
def get_whale_holdings(token_mint):
    """
    Returns the % of total supply held by the largest holder.
    """
    holders, total_sup = get_largest_token_holders_with_total_supply(token_mint, top_n=1)
    if holders and total_sup > 0:
        return float(holders[0][2])  # holders[0][2] => % of total
    return 0.0

@st.cache_data(ttl=600)
def get_token_info(token_mint):
    """
    Example call to a placeholder external API for token info.
    You can adapt to your real endpoint.
    """
    url = f"https://frontend-api.pump.fun/coins/{token_mint}?sync=true"
    try:
        response = requests.get(url, headers={"accept": "*/*"}, timeout=10)
        response.raise_for_status()
        data = response.json()
        return {
            "name": data.get("name", "Unknown"),
            "symbol": data.get("symbol", "UNKNOWN"),
            "total_supply": float(data.get("total_supply", 0)),
            "usd_market_cap": float(data.get("usd_market_cap", 0)),
            "creator": data.get("creator", "Unknown")
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching token info for {token_mint}: {e}")
        return {
            "name": "Unknown",
            "symbol": "UNKNOWN",
            "total_supply": 0.0,
            "usd_market_cap": 0.0,
            "creator": "Unknown"
        }

def is_valid_pubkey(pubkey):
    """
    Quick regex check for a valid Solana pubkey format (32-44 base58 chars).
    """
    return bool(re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}', pubkey))

def monitor_transfers():
    """
    Monitor transfers for addresses in session state.
    Only runs if there are monitored addresses.
    """
    # Load monitored addresses from session state
    monitored = load_monitored_addresses()

    # Check if there are any addresses to monitor
    if not monitored:
        logger.info("No monitored addresses in session state. Skipping monitoring.")
        return  # Exit if no addresses are being monitored

    logger.info(f"Monitoring {len(monitored)} addresses.")

    # Load eligible tokens and history
    eligible = load_eligible_tokens()
    koh_history = load_king_of_the_hill_history()

    for addr in monitored:
        txs = get_recent_transactions(addr)  # Fetch recent transactions for the address
        for tx in txs:
            actions = tx.get("actions", [])
            for action in actions:
                info = action.get("info", {})
                token_mint = info.get("token_address", "")

                # Fetch market cap and liquidity for token
                mc_usd = get_market_cap(token_mint)
                liq = get_liquidity(token_mint)

                # Fetch creator (developer wallet) using the mint
                dev_wallet = fetch_creator_wallet(token_mint)
                if not dev_wallet:
                    logger.warning(f"No dev wallet found for mint: {token_mint}. Skipping.")
                    continue

                # Fetch total supply
                dev_wallet = fetch_creator_wallet(token_mint)
                if not dev_wallet:
                    logger.warning(f"No dev wallet found for mint: {token_mint}. Skipping.")
                    continue
                token_details = fetch_token_details(token_mint,dev_wallet)
                total_supply = Decimal(token_details.get("total_supply", 0)) if token_details else Decimal(0)

                # Calculate dev holdings
                if total_supply > 0:
                    dev_hold = Decimal(get_token_holdings(token_mint, dev_wallet))
                    dev_hold_percent = (dev_hold / total_supply) * 100
                else:
                    dev_hold = Decimal(0)
                    dev_hold_percent = Decimal(0)

                # Calculate whale holdings
                w_hold = get_whale_holdings(token_mint)

                # Sample criteria => adapt as needed
                if liq >= 29000 and mc_usd <= 102000 and dev_hold <= 0 and w_hold <= 3:
                    # Add token to eligible list if it's new
                    if not any(t["Mint Address"] == token_mint for t in eligible):
                        tinfo = get_token_info(token_mint)
                        usd_p = Decimal(tinfo.get("usd_market_cap", 0)) / Decimal(tinfo.get("total_supply", 1))
                        eligible.append({
                            "Token Name": tinfo.get("name", "Unknown"),
                            "Symbol": tinfo.get("symbol", "UNKNOWN"),
                            "Mint Address": token_mint,
                            "Market Cap (USD)": float(mc_usd),
                            "Liquidity (USD)": float(liq),
                            "Whale Holdings (%)": float(w_hold),
                            "Dev Holdings (%)": float(dev_hold_percent),
                            "usd_price": float(usd_p),
                            "velocity": 0.0
                        })

    # Save eligible tokens after processing
    save_eligible_tokens(eligible)
    logger.info(f"Monitoring complete. Found {len(eligible)} eligible tokens.")


################### SYNC ANY NEW TOKENS => KOH HISTORY #################
def fetch_token_details(mint_address, dev_wallet):
    """
    Fetch token details such as total supply using mint and dev wallet.
    """
    if not mint_address or not is_valid_pubkey(mint_address):
        logger.error(f"Invalid mint address passed to fetch_token_details: {mint_address}")
        return None

    if not dev_wallet or not is_valid_pubkey(dev_wallet):
        logger.error(f"Invalid dev wallet address passed to fetch_token_details: {dev_wallet}")
        return None

    # Fetch additional details from the frontend API
    url = f"https://frontend-api.pump.fun/coins/{mint_address}?sync=true"
    try:
        response = requests.get(url, headers={'accept': '*/*'}, timeout=10)
        response.raise_for_status()
        data = response.json()
        total_supply = float(data.get("total_supply", 0))
        return {
            "creator": dev_wallet,
            "total_supply": total_supply,
            "name": data.get("name", "Unknown"),
            "symbol": data.get("symbol", "UNKNOWN"),
        }
    except requests.RequestException as e:
        logger.error(f"Failed to fetch token details for {mint_address}: {e}")
        return None



def sync_tokens_to_koh_history():
    """
    Sync newly discovered tokens into KOH_HISTORY_FILE so they appear in the left sidebar.
    This function only runs if `tracked_tokens` exists and contains valid data.
    """
    # Check if tracked_tokens exists and is not empty
    if "tracked_tokens" not in st.session_state or not st.session_state["tracked_tokens"]:
        logger.info("No tracked tokens in session state. Skipping sync_tokens_to_koh_history.")
        return  # Skip execution if no tokens are tracked

    # Load current history from file
    current_history = load_king_of_the_hill_history()
    existing_symbols = {h["symbol"] for h in current_history if "symbol" in h}
    existing_mints = {h["mint"] for h in current_history if "mint" in h}
    from time import strftime, localtime

    # Process each tracked token
    for symbol, detail in st.session_state["tracked_tokens"].items():
        if symbol not in existing_symbols and detail["address"] not in existing_mints:
            # Validate the mint address before making API calls
            mint_address = detail.get("address", "")
            if not mint_address or not is_valid_pubkey(mint_address):
                logger.warning(f"Invalid mint address: {mint_address}. Skipping.")
                continue

            # Fetch token details
            dev_wallet = fetch_creator_wallet(mint_address)
            if not dev_wallet:
                logger.warning(f"No dev wallet found for mint: {mint_address}. Skipping.")
                continue
            token_details = fetch_token_details(mint_address, dev_wallet)
            if token_details:
                new_record = {
                    "name": token_details.get("name", "Unknown"),
                    "symbol": token_details.get("symbol", symbol),
                    "mint": token_details.get("mint", mint_address),
                    "creator": token_details.get("creator", "Unknown"),
                    "usd_market_cap": float(token_details.get("usd_market_cap", detail.get("market_cap", 0))),
                    "total_supply": float(token_details.get("total_supply", 0.0)),
                    "usd_price": float(token_details.get("usd_price", 0.0)),
                    "timestamp": strftime("%Y-%m-%d %H:%M:%S", localtime())
                }
            else:
                # Use fallback data if API call fails
                logger.error(f"Failed to fetch token details for mint: {mint_address}")
                new_record = {
                    "name": symbol,
                    "symbol": symbol,
                    "mint": mint_address,
                    "creator": "Unknown",
                    "usd_market_cap": float(detail.get("market_cap", 0)),
                    "total_supply": 0.0,
                    "usd_price": 0.0,
                    "timestamp": strftime("%Y-%m-%d %H:%M:%S", localtime())
                }

            # Add new record to history
            current_history.insert(0, new_record)
            existing_symbols.add(symbol)
            existing_mints.add(mint_address)

    # Save the updated history back to the file
    save_king_of_the_hill_history(current_history)
    logger.info("sync_tokens_to_koh_history completed successfully.")


################### INITIALIZE SESSION KEYS ###############
def session_init():
    """
    Initialize certain st.session_state keys.
    """
    if "tracked_tokens" not in st.session_state:
        st.session_state["tracked_tokens"] = {}
    if "graduated_tokens" not in st.session_state:
        st.session_state["graduated_tokens"] = []
    if "market_cap_history" not in st.session_state:
        st.session_state["market_cap_history"] = {}
    if "velocities" not in st.session_state:
        st.session_state["velocities"] = {}
    if "selected_token" not in st.session_state:
        st.session_state["selected_token"] = None
    if "show_animation" not in st.session_state:
        st.session_state.show_animation = True  # Initialize animation flag
    if "tg_popularity" not in st.session_state:
        st.session_state["tg_popularity"] = {}
################### FUNCTION TO LOAD LOTTIE ANIMATION ################
def load_lottiefile(filepath: str):
    """Load a Lottie animation from a JSON file."""
    with open(filepath, "r") as f:
        return json.load(f)

################### MAIN APP ###############################
def main():
    add_custom_styling()
    session_init()

    # Check if animation needs to be displayed
    if st.session_state.show_animation:
        # Path to your Lottie animation file
        lottie_animation_path = os.path.join("assets", "warp_speed.json")

        if not os.path.exists(lottie_animation_path):
            st.error(f"Lottie animation file not found at path: {lottie_animation_path}")
            st.stop()
        else:
            # Load the animation
            lottie_animation = load_lottiefile(lottie_animation_path)

            # Hide Streamlit's default menu and footer for a cleaner appearance
            hide_menu_style = """
                <style>
                #MainMenu {visibility: hidden;}
                footer {visibility: hidden;}
                </style>
                """
            st.markdown(hide_menu_style, unsafe_allow_html=True)

            # Create a container for centering the animation
            with st.container():
                cols = st.columns([1, 2, 1])  # Create three columns
                with cols[1]:
                    st_lottie(
                        lottie_animation,
                        speed=1,
                        height=600,  # Adjust height as needed
                        key="warp_speed"
                    )

            # Display a progress bar or message during the animation display time
            progress_text = "Your future awaits"
            progress_bar = st.progress(0, text=progress_text)  # Removed 'key' parameter

            for percent_complete in range(100):
                time.sleep(0.05)  # Total of ~5 seconds
                progress_bar.progress(percent_complete + 1, text=progress_text)
            progress_bar.empty()

            # Add a "Skip Animation" button
            with st.container():
                cols = st.columns([1, 2, 1])
                with cols[1]:
                    if st.button("Skip Animation", key="skip_animation_button"):
                        st.session_state.show_animation = False
                        st_autorefresh(interval=100, limit=1, key="skip_animation_autorefresh")

            # Hide the animation and show the main app
            st.session_state.show_animation = False

            # Trigger a refresh after the animation by using st_autorefresh with a longer interval
            st_autorefresh(interval=6000, limit=1, key="animation_autorefresh")

    else:
        # Proceed with the main app content

        # Menu Navigation
        menu = ["Overview", "King of the Hill", "Token Analysis", "Criteria Monitoring"]
        chosen = st.sidebar.radio("Menu", menu, index=0)

        # Auto-refresh every 5 minutes
        st_autorefresh(interval=300_000, limit=100, key="auto_refresh_holding")
        # After each refresh => monitor
        monitor_transfers()

        # KoH tokens in sidebar
        st.sidebar.write("---")
        st.sidebar.header("🔎 King of the Hill History")
        history = load_king_of_the_hill_history()
        if history:
            opt_labels = [f"{h['name']} ({h['symbol']})" for h in history]
            picked_label = st.sidebar.selectbox("Pick a KoH token for analysis:", opt_labels)
            if picked_label:
                idx = opt_labels.index(picked_label)
                st.session_state["selected_token"] = history[idx]
        else:
            st.sidebar.info("No KoH history yet. Possibly run King of the Hill or Surveillance dashboard?")

        ########################################################
        #                   OVERVIEW PAGE
        ########################################################
        if chosen == "Overview":
            st.title("Welcome to the Full Holding Dashboard!")
            st.markdown("""
            **Full Holding Dashboard** is designed to provide comprehensive insights into your monitored Solana wallets and tokens. Navigate through the menu to explore various features:
            
            - **Overview:** Get a quick summary of the dashboard.
            - **King of the Hill:** Access the Surveillance Dashboard to monitor top tokens.
            - **Token Analysis:** Dive deep into specific token analytics.
            - **Criteria Monitoring:** Manage your monitored addresses and track tokens that meet specific criteria.
            """)
            st.markdown("### Choose an option from the sidebar to get started.")

        ########################################################
        #                KING OF THE HILL PAGE
        ########################################################
        elif chosen == "King of the Hill":
            st.title("👑 King of the Hill => Surveillance Dashboard")
            # Move the import here, after the animation
            try:
                from surveillance import run_surveillance_dashboard
            except ImportError as e:
                run_surveillance_dashboard = None
                logger.error(f"Could not import `run_surveillance_dashboard` => {e}")
                st.error("Could not import or run the surveillance dashboard. Check import?")
                return  # Exit early if import fails

            if run_surveillance_dashboard:
                with st.spinner("Loading Token Surveillance Dashboard..."):
                    run_surveillance_dashboard()
                sync_tokens_to_koh_history()  # update KoH history if new tokens appear
            else:
                st.error("Could not import or run the surveillance dashboard. Check import?")

        ########################################################
        #                TOKEN ANALYSIS PAGE
        ########################################################
        elif chosen == "Token Analysis":
            st.title("Token Analysis (Steps 0–4)")
            if not st.session_state["selected_token"]:
                st.info("No token selected. Please pick one from KoH History in the sidebar.")
                st.stop()

            token = st.session_state["selected_token"]
            st.subheader(f"Analyzing: {token['name']} ({token['symbol']})")
            st.write(f"**Mint Address:** `{token.get('mint','N/A')}`")
            st.write(f"**Dev Wallet (Creator):** `{token.get('creator','N/A')}`")
            st.write(f"**Timestamp:** {token.get('timestamp','N/A')}`")

            # Step 0 => On-chain check
            st.header("Step 0: On-Chain RPC Check")
            test_pl = {"jsonrpc": "2.0", "id": 1, "method": "getVersion", "params": []}
            test_resp = send_rpc_request(test_pl)
            if test_resp and "result" in test_resp:
                st.success("✅ On-chain RPC connected.")
            else:
                st.error("❌ Could not connect on-chain. Stopping.")
                st.stop()

            # Step 1 => Largest Holders
            st.header("Step 1: Largest Holders 📊")
            top_n = st.slider("Number of top holders", 5, 50, 20, key="top_holders_slider")
            with st.spinner("🔍 Loading largest holders..."):
                largest_holders, total_supply = get_largest_token_holders_with_total_supply(token["mint"], top_n)
            if not largest_holders:
                st.error("No largest holders found. Possibly an invalid mint address.")
                st.stop()

            st.markdown(f"**Total Supply:** {float(total_supply):,.4f} tokens")
            df = pd.DataFrame(largest_holders, columns=["Holder Address", "Amount", "% of Total"])
            style_df = df.style.format({
                "Amount": "{:.4f}",
                "% of Total": "{:.2f}%"
            }).highlight_max(subset=["Amount"], color="#a7ffeb").background_gradient(cmap="Blues")
            st.dataframe(style_df, use_container_width=True)

            # Pie chart => top 10 + "Others"
            top10 = df.head(10).copy()
            sum_others = df["Amount"].sum() - top10["Amount"].sum()
            if sum_others > 0:
                float_sum_others = float(sum_others)
                float_total_supply = float(total_supply)
                top10.loc[len(top10)] = [
                    "Others",
                    float_sum_others,
                    (float_sum_others / float_total_supply) * 100
                ]
            fig_pie = px.pie(top10, names="Holder Address", values="Amount",
                            title="Largest Token Holders Distribution (Top 10 + Others)")
            st.plotly_chart(fig_pie, use_container_width=True)

            # Step 2 => # of signatures
            st.header("Step 2: Choose Number of Signatures to Fetch 📝")
            user_sig_count = st.number_input("Signatures to fetch per holder:", 1, 500, 10, key="user_sig_count")

            st.subheader("Dependent Dropdown for Each Holder's Signatures")
            if len(largest_holders) > 1:
                holder_list = [row[0] for row in largest_holders]
                chosen_holder = st.selectbox("Choose a holder to see inbound from BC:", holder_list, key="chosen_holder_selectbox")
                if chosen_holder:
                    with st.spinner("Fetching inbound from BC..."):
                        total_recv, inbound_sigs = check_received_from_bonding_curve_via_shyft(
                            holder_addr_str=chosen_holder,
                            bonding_curve_str="",  # Insert real BC address if needed
                            target_mint_str=token["mint"],
                            tx_num=int(user_sig_count)
                        )
                    st.write(f"**Total Received** => {float(total_recv):,.4f}, # TX => {len(inbound_sigs)}")
                    if inbound_sigs:
                        dd_opts = [f"{sig} => {amt_str}" for (sig, amt_str) in inbound_sigs]
                        pick_sig = st.selectbox("Pick a transaction signature:", dd_opts, key="pick_sig_selectbox")
                        if pick_sig:
                            st.write(f"You chose => `{pick_sig}`")
                    else:
                        st.info("No inbound TX from bonding curve found.")
            else:
                st.info("Only 1 holder. Dropdown not applicable.")

            # Step 3 => Dev Wallet outgoing
            st.header("Step 3: Dev Wallet Outgoing 🚀")
            dev_addr = token.get("creator", "N/A")
            st.write(f"Dev wallet => `{dev_addr}` with signature count => {user_sig_count}")

            # Corrected progress bar usage: 'text' is a descriptive message
            prg = st.progress(0, text="Processing Dev Wallet Outgoing Transactions")
            for i in range(5):
                time.sleep(0.1)
                prg.progress((i+1)*20, text="Processing Dev Wallet Outgoing Transactions")
            prg.empty()

            dev_total_out, dev_data = check_outgoing_via_shyft(dev_addr, token["mint"], int(user_sig_count))
            st.write(f"**Dev Wallet Outgoing** => {float(dev_total_out):,.4f} tokens, # TX => {len(dev_data)}")
            if dev_data:
                with st.expander("Show Dev Wallet Outgoing TX"):
                    for (sig, amt_str) in dev_data:
                        st.write(f"Sig => {sig}, Amount => {amt_str}")
            else:
                st.info("No dev wallet outgoing found for that signature count & mint.")

            # Step 4 => Arbitrary Wallet inbound from BC
            st.header("Step 4: Arbitrary Wallet => inbound from BC 🔄")
            arbitrary_wallet = st.text_input("Enter a wallet pubkey to see inbound from BC:", key="arbitrary_wallet_input")
            if arbitrary_wallet:
                if not is_valid_pubkey(arbitrary_wallet):
                    st.error("Invalid Solana address format.")
                else:
                    with st.spinner("Checking inbound from BC..."):
                        inbound_tot, inbound_data = check_received_from_bonding_curve_via_shyft(
                            holder_addr_str=arbitrary_wallet,
                            bonding_curve_str="",  # Insert real BC address if needed
                            target_mint_str=token["mint"],
                            tx_num=int(user_sig_count)
                        )
                    st.write(f"**Inbound** => {float(inbound_tot):,.4f}, # TX => {len(inbound_data)}")
                    if inbound_data:
                        with st.expander("Inbound TX details"):
                            for (sig, amt_str) in inbound_data:
                                st.write(f"Sig: {sig}, Amount: {amt_str}")
                    else:
                        st.info("No inbound TX found for that wallet.")

            with st.expander("💬 Provide Feedback"):
                fb_txt = st.text_area("Your Feedback:", key="feedback_textarea")
                if st.button("Submit Feedback", key="submit_feedback_button"):
                    st.success("Thank you for your feedback.")

            st.success("✅ Token Analysis steps finished.")

        ########################################################
        #               CRITERIA MONITORING PAGE
        ########################################################
        elif chosen == "Criteria Monitoring":
            st.title("🔍 Criteria Monitoring")

            # Manage addresses
            st.subheader("Manage Monitored Addresses")
            addy_list = load_monitored_addresses()
            new_a = st.text_input("Add a new contract/wallet address:", key="new_address_input")
            if st.button("Add Address", key="add_address_button"):
                if new_a and is_valid_pubkey(new_a):
                    if new_a not in addy_list:
                        addy_list.append(new_a)
                        save_monitored_addresses(addy_list)
                        st.success(f"Address `{new_a}` added!")
                    else:
                        st.warning("Address is already in the list.")
                else:
                    st.error("Invalid Solana address.")

            if addy_list:
                st.markdown("**Monitored Addresses**")
                for a in addy_list:
                    c = st.columns([0.9, 0.1])
                    c[0].write(a)
                    if c[1].button("Remove", key=f"remove_{a}"):
                        addy_list.remove(a)
                        save_monitored_addresses(addy_list)
                        st.success(f"Removed => {a}")
            else:
                st.info("No addresses are currently being monitored.")

            st.markdown("---")
            st.subheader("Eligible Tokens Passing Criteria")
            eligible = load_eligible_tokens()
            hist = load_king_of_the_hill_history()
            if eligible:
                # Update velocity if we have at least 2 KoH records
                for t in eligible:
                    match_hist = [h for h in hist if h["mint"] == t["Mint Address"]]
                    if len(match_hist) >= 2:
                        vel = calculate_velocity(match_hist, 0, 1)
                        t["velocity"] = float(vel)
                    else:
                        t["velocity"] = 0.0

                eligible_sorted = sorted(eligible, key=lambda x: x["velocity"], reverse=True)
                save_eligible_tokens(eligible_sorted)
                df_eligible = pd.DataFrame(eligible_sorted)

                st_elig = df_eligible.style.format({
                    "Market Cap (USD)": "${:,.2f}",
                    "Liquidity (USD)": "${:,.2f}",
                    "Whale Holdings (%)": "{:.2f}%",
                    "Dev Holdings (%)": "{:.2f}%",
                    "velocity": "{:+.2f}%"
                }).background_gradient(subset=["velocity"], cmap="coolwarm")\
                  .highlight_max(subset=["velocity"], color="#a7ffeb")\
                  .highlight_min(subset=["velocity"], color="#ff7f7f")
                st.dataframe(st_elig, use_container_width=True)

                fig_bar = px.bar(
                    df_eligible,
                    x="Symbol",
                    y="velocity",
                    color="velocity",
                    color_continuous_scale="RdYlGn",
                    title="Eligible Tokens by Velocity",
                    labels={"velocity": "Velocity (%)"}
                )
                st.plotly_chart(fig_bar, use_container_width=True)

                csv_elig = df_eligible.to_csv(index=False).encode("utf-8")
                st.download_button("📥 Download Eligible as CSV", csv_elig, "eligible_tokens.csv", "text/csv")
            else:
                st.info("No tokens meet the preset criteria.")

            st.markdown("---")
            st.subheader("All Monitored Tokens")
            all_toks = load_all_monitored_tokens()
            if all_toks:
                df_all = pd.DataFrame(all_toks)
                st.dataframe(df_all, use_container_width=True)
            else:
                st.info("No tokens are being monitored currently.")

            st.markdown("---")
            st.info("The system checks for eligible tokens every 5 minutes based on the monitored addresses.")

            if st.button("🔄 Refresh Monitoring Now", key="refresh_monitoring_button"):
                with st.spinner("Evaluating tokens..."):
                    monitor_transfers()
                st.success("✅ Monitoring refreshed successfully!")

            # logs
            st.subheader("📜 Monitoring Logs")
            if os.path.exists("app.log"):
                with open("app.log", "r") as f:
                    logs = f.read()
                st.text_area("Application Logs:", logs, height=300)
            else:
                st.info("No logs available yet.")


if __name__ == "__main__":
    main()

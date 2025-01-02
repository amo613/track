# surveillance.py

import time
import streamlit as st
import pandas as pd
import plotly.express as px
import logging
import random
import re
import os
import json
from decimal import Decimal
import requests  # Ensure requests is imported
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker  # Updated import
from textblob import TextBlob
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import UserAlreadyParticipantError, SessionPasswordNeededError
import tkinter as tk
from tkinter import simpledialog, messagebox
import threading
import asyncio  # Import asyncio

# ---------------------- CONFIGURATION ---------------------- #

# Configure Logging
logging.basicConfig(
    level=logging.INFO,  # Set to DEBUG for more detailed logs
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("surveillance.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Configure Streamlit page
# st.set_page_config(page_title="Token Surveillance Dashboard", page_icon="🪙", layout="wide")

# CSS Styling (Optional)
st.markdown("""
<style>
/* Your existing CSS styles */
</style>
""", unsafe_allow_html=True)

# ---------------------- TELEGRAM CREDENTIALS ---------------------- #
# **IMPORTANT:** Replace the placeholders with your actual credentials.
TELEGRAM_API_ID = '25385375'  # Replace with your actual API ID
TELEGRAM_API_HASH = 'f5b45903d075372e12018e527d2ee5fc'  # Replace with your actual API Hash
SESSION_FILE = 'session.txt'  # File to store the StringSession

# ---------------------- DATABASE SETUP ---------------------- #

# Initialize SQLAlchemy ORM for data persistence
Base = declarative_base()

class ScrapedData(Base):
    __tablename__ = 'scraped_data'
    id = Column(Integer, primary_key=True)
    channel = Column(String)
    word = Column(String)
    count = Column(Integer)
    avg_sentiment = Column(Float)
    timestamp = Column(DateTime, default=time.strftime("%Y-%m-%d %H:%M:%S"))

# Create an engine and session
engine = create_engine('sqlite:///scraped_data.db', connect_args={'check_same_thread': False})
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)
session_db = SessionLocal()

# ---------------------- SESSION STATE INITIALIZATION ---------------------- #
# Removed redundant session_state initializations as holding.py handles them

# ---------------------- API FETCHING HELPERS ---------------------- #

API_URL = "https://frontend-api.pump.fun/coins/king-of-the-hill?includeNsfw=includeNsfw"
TOKEN_INFO_URL_TEMPLATE = "https://data.solanatracker.io/tokens/{token_address}"
HOLDERS_URL_TEMPLATE = "https://frontend-api.pump.fun/trades/all/{mint}?limit=10&offset=0&minimumSize=1"
SOLANA_RPC_ENDPOINT = "https://rpc.shyft.to?api_key=738Ve00HEdkbrj31"  # Ensure this is correct
SHYFT_API_KEY = "738Ve00HEdkbrj31"
SOLANATRACKER_API_KEY = "88b62348-8906-4499-9ee7-a374f115da0e"
MARKET_CAP_THRESHOLD = 410  # in SOL
INACTIVITY_LIMIT = 3
FETCH_INTERVAL = 5  # in minutes
HISTORY_LENGTH = 20
GRAPH_THRESHOLD = 390  # in SOL

def send_rpc_request(payload, retries=5, backoff_factor=0.5, max_backoff=16):
    """Sends an RPC request to the SOLANA_RPC_ENDPOINT with retry logic."""
    for attempt in range(retries):
        try:
            r = requests.post(SOLANA_RPC_ENDPOINT, json=payload)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait_time = min(backoff_factor * (2**attempt), max_backoff)
                time.sleep(wait_time + random.uniform(0, 0.3))
            else:
                r.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"send_rpc_request => {e}")
            time.sleep(min(backoff_factor*(2**attempt), max_backoff))
    logger.error("Max retries in send_rpc_request.")
    return {}

def get_token_holdings(token_mint, wallet_addr):
    """
    Fetches the total token holdings of a specific wallet for a given token mint,
    summing all relevant token accounts.
    """
    pl = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            wallet_addr,
            {"mint": token_mint},
            {"encoding": "jsonParsed"}
        ]
    }
    resp = send_rpc_request(pl)
    if not resp or "result" not in resp:
        logger.error(f"Failed to get token holdings for wallet {wallet_addr} and mint {token_mint}.")
        return 0.0
    total_amt = Decimal(0)
    for acct in resp["result"]["value"]:
        info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        bal = info.get("tokenAmount", {}).get("uiAmount", 0)
        total_amt += Decimal(bal)
    return float(total_amt)

def fetch_king_of_the_hill_tokens():
    """Fetches the list of King of the Hill tokens from the specified API."""
    try:
        response = requests.get(API_URL, headers={"accept": "application/json"}, timeout=10)
        response.raise_for_status()
        tokens = response.json()
        if isinstance(tokens, dict):
            tokens = [tokens]
        result = []
        for token in tokens:
            symbol = token.get("symbol")
            # Use 'market_cap' directly as per API response (in SOL)
            market_cap = token.get("market_cap", 0)
            address = token.get("mint", "N/A")
            if symbol is not None and address != "N/A":
                result.append({
                    "symbol": symbol,
                    "market_cap": market_cap,
                    "address": address
                })
            else:
                logger.warning(f"Skipping token with symbol '{symbol}' due to invalid address '{address}'.")
        return result
    except Exception as e:
        st.error(f"Error fetching tokens: {e}")
        logger.error(f"Error fetching tokens: {e}")
        return []

def fetch_top_holders(mint):
    """Fetches the top token holders (trades) from the specified endpoint."""
    try:
        url = HOLDERS_URL_TEMPLATE.format(mint=mint)
        response = requests.get(url, headers={"accept": "application/json"}, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            data = [data]
        data_sorted = sorted(data, key=lambda x: x.get("token_amount", 0), reverse=True)
        return data_sorted[:5]
    except Exception as e:
        st.error(f"Error fetching top holders: {e}")
        logger.error(f"Error fetching top holders for mint {mint}: {e}")
        return []

def fetch_token_info(token_address, api_key):
    """Fetch token name and liquidity information from SolanaTracker API."""
    url = f"https://data.solanatracker.io/tokens/{token_address}"
    headers = {
        "accept": "application/json",
        "x-api-key": api_key
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Extract token name
        token_name = data.get("token", {}).get("name", "N/A")

        # Extract liquidity information
        pools = data.get("pools", [])
        liquidity_info = []
        for pool in pools:
            market = pool.get("market", "N/A")
            liquidity_usd = pool.get("liquidity", {}).get("usd", 0)
            liquidity_info.append({
                "market": market,
                "liquidity_usd": liquidity_usd
            })

        # Calculate total liquidity
        total_liquidity_usd = sum(pool['liquidity_usd'] for pool in liquidity_info)

        return {
            "token_name": token_name,
            "liquidity_info": liquidity_info,
            "total_liquidity_usd": total_liquidity_usd
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching token information for {token_address}: {e}")
        return None

def fetch_token_details(mint):
    """Fetch token details such as creator and total supply."""
    url = f"https://frontend-api.pump.fun/coins/{mint}?sync=true"
    try:
        response = requests.get(url, headers={'accept': '*/*'}, timeout=10)
        response.raise_for_status()
        data = response.json()
        creator = data.get("creator", "Unknown")
        total_supply = float(data.get("total_supply", 0))
        return {
            "creator": creator,
            "total_supply": total_supply
        }
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching token details for {mint}: {e}")
        logger.error(f"Error fetching token details for mint {mint}: {e}")
        return {
            "creator": "Unknown",
            "total_supply": 0.0
        }

# ---------------------- TKINTER AUTHENTICATION ---------------------- #

import os
import tkinter as tk
from tkinter import simpledialog, messagebox

def launch_authentication_window():
    if os.environ.get('DISPLAY', '') == '':
        print("No display found. Using terminal for authentication.")
        phone_number = input("Enter your Telegram phone number: ")
        if phone_number:
            # Proceed with the authentication process
            pass
        else:
            print("Error: Phone number is required for authentication.")
        return

    root = tk.Tk()
    root.withdraw()  # Hide the main window
    phone_number = simpledialog.askstring("Telegram Authentication", "Enter your Telegram phone number:")
    if phone_number:
        # Proceed with the authentication process
        pass
    else:
        messagebox.showerror("Error", "Phone number is required for authentication.")

    def authenticate():
        code = code_entry.get()
        password = password_entry.get()
        client = st.session_state.get("temp_client", None)
        if not client:
            messagebox.showerror("Error", "No client found. Please request the code first.")
            return
        if not code:
            messagebox.showerror("Error", "Please enter the code received via Telegram.")
            return
        try:
            client.sign_in(code=code)
            # At this point, if two-step verification is not enabled, sign_in is successful
            string_session = client.session.save()
            with open(SESSION_FILE, 'w') as f:
                f.write(string_session)
            messagebox.showinfo("Success", "Authentication successful! Session saved.")
            st.session_state["authenticated"] = True
            logger.info("Telegram authentication successful and session saved.")
            client.disconnect()
            root.destroy()
        except SessionPasswordNeededError:
            if not password:
                messagebox.showerror("Error", "Two-step verification is enabled. Please enter your password.")
                return
            try:
                client.sign_in(password=password)
                string_session = client.session.save()
                with open(SESSION_FILE, 'w') as f:
                    f.write(string_session)
                messagebox.showinfo("Success", "Authentication successful! Session saved.")
                st.session_state["authenticated"] = True
                logger.info("Telegram authentication successful and session saved.")
                client.disconnect()
                root.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"Authentication failed: {e}")
                logger.error(f"Authentication failed: {e}")
        except Exception as e:
            messagebox.showerror("Error", f"Authentication failed: {e}")
            logger.error(f"Authentication failed: {e}")

    def on_closing():
        # Ensure client is disconnected if window is closed
        client = st.session_state.get("temp_client", None)
        if client:
            client.disconnect()
        root.destroy()

    # Initialize Tkinter root
    root = tk.Tk()
    root.title("Telegram Authentication")
    root.geometry("400x300")
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", on_closing)

    # Step 1: Enter Phone Number
    tk.Label(root, text="Step 1: Enter Telegram Phone Number", font=("Helvetica", 12, "bold")).pack(pady=(20, 5))
    phone_entry = tk.Entry(root, width=30)
    phone_entry.pack(pady=(5, 10))

    # Send Code Button
    send_code_button = tk.Button(root, text="Send Code", command=send_code, bg="#3498db", fg="white")
    send_code_button.pack(pady=(5, 20))

    # Step 2: Enter Code and Password
    tk.Label(root, text="Step 2: Enter Code and Password", font=("Helvetica", 12, "bold")).pack(pady=(10, 5))
    code_entry = tk.Entry(root, width=30)
    code_entry.pack(pady=(5, 10))
    tk.Label(root, text="Enter Password (if two-step verification):", font=("Helvetica", 10)).pack(pady=(5, 5))
    password_entry = tk.Entry(root, width=30, show="*")
    password_entry.pack(pady=(5, 10))

    # Authenticate Button
    authenticate_button = tk.Button(root, text="Authenticate", command=authenticate, bg="#2ecc71", fg="white")
    authenticate_button.pack(pady=(10, 20))
    authenticate_button.config(state='disabled')  # Initially disabled until code is sent

    root.mainloop()

# ---------------------- SURVEILLANCE LOGIC ---------------------- #

def update_data(client):
    """Fetches and updates the tracked tokens data."""
    new_tokens = fetch_king_of_the_hill_tokens()
    tracked_tokens = st.session_state["tracked_tokens"]
    market_cap_history = st.session_state["market_cap_history"]

    for token in new_tokens:
        symbol = token["symbol"]
        market_cap = token["market_cap"]
        address = token["address"]

        if market_cap >= MARKET_CAP_THRESHOLD:
            continue

        if symbol not in tracked_tokens:
            token_details = fetch_token_details(address)
            creator = token_details["creator"]
            total_supply = token_details["total_supply"]

            # Fetch token info from SolanaTracker API
            token_info = fetch_token_info(address, SOLANATRACKER_API_KEY)
            if token_info:
                token_name = token_info["token_name"]
                liquidity_usd = token_info["total_liquidity_usd"]
            else:
                token_name = "N/A"
                liquidity_usd = 0.0

            if creator == "Unknown" or total_supply == 0.0:
                dev_holdings = 0.0
                dev_holding_percent = 0.0
            else:
                dev_holdings = get_token_holdings(address, creator)
                if total_supply > 0:
                    dev_holding_percent = (Decimal(dev_holdings) / Decimal(total_supply)) * 100
                else:
                    dev_holding_percent = Decimal(0.0)

            tracked_tokens[symbol] = {
                "symbol": symbol,
                "market_cap": market_cap,
                "inactivity_count": 0,
                "address": address,
                "dev_holdings": float(dev_holdings),
                "dev_holding_percent": float(dev_holding_percent),
                "name": token_name,
                "liquidity_usd": liquidity_usd,
                "creator_wallet": creator,
                "tg_popularity": 0  # Initialize Telegram popularity
            }
            market_cap_history[symbol] = [market_cap]
            logger.info(f"Added token {symbol} ({token_name}) with Dev Holding (%): {dev_holding_percent:.2f}% and Liquidity: ${liquidity_usd:,.2f}")
        else:
            # Update existing token details if necessary
            current_cap = tracked_tokens[symbol]["market_cap"]
            tracked_tokens[symbol]["market_cap"] = market_cap
            tracked_tokens[symbol]["inactivity_count"] += 1  # Example update
            market_cap_history[symbol].append(market_cap)
            logger.info(f"Updated token {symbol} with new Market Cap: {market_cap} SOL")

    # Remove inactive tokens
    for symbol, details in list(tracked_tokens.items()):
        if details["inactivity_count"] >= INACTIVITY_LIMIT:
            del tracked_tokens[symbol]
            del market_cap_history[symbol]
            if symbol in st.session_state["tg_popularity"]:
                del st.session_state["tg_popularity"][symbol]
            logger.info(f"Removed inactive token {symbol} due to inactivity count {details['inactivity_count']}")

    # Calculate velocities as percentage change
    velocities = {}
    for symbol, caps in market_cap_history.items():
        if len(caps) > 1:
            current_cap = caps[-1]
            previous_cap = caps[-2]
            if previous_cap != 0:
                velocity_pct = ((current_cap - previous_cap) / previous_cap) * 100
                velocities[symbol] = velocity_pct
            else:
                velocities[symbol] = 0.0  # Avoid division by zero
                logger.warning(f"Previous market cap for {symbol} is zero. Setting velocity to 0%.")
        else:
            velocities[symbol] = 0.0
    st.session_state["velocities"] = velocities

    # Fetch top holders for each tracked token
    for s, d in tracked_tokens.items():
        top_holders = fetch_top_holders(d["address"])
        if top_holders:
            holders_str = []
            for h in top_holders:
                user = h.get("user", "Unknown")
                amount = h.get("token_amount", 0)
                holders_str.append(f"{user}: {amount}")
            d["top_holders_str"] = "; ".join(holders_str)
        else:
            d["top_holders_str"] = "N/A"

def scrape_and_update_popularity(client):
    """Scrapes Telegram for word popularity and updates the session state."""
    tracked_tokens = st.session_state["tracked_tokens"]
    tg_popularity = st.session_state["tg_popularity"]

    for symbol, details in tracked_tokens.items():
        word = symbol  # Define the word to scrape, here using the token symbol
        try:
            dialogs = client.get_dialogs()
            total_count = 0
            for dialog in dialogs:
                if dialog.is_group or dialog.is_channel:
                    messages = client.get_messages(dialog.entity, limit=100)
                    for message in messages:
                        if message.text:
                            total_count += len(re.findall(rf'\b{re.escape(word)}\b', message.text, re.IGNORECASE))
            popularity = total_count // 1000  # 1:1000 scaling
            tg_popularity[symbol] = int(popularity)
            logger.info(f"Token {symbol}: Scraped count = {total_count}, Popularity = {popularity}")
        except Exception as e:
            st.error(f"Error scraping Telegram for word '{word}': {e}")
            logger.error(f"Error scraping Telegram for word '{word}': {e}")
            tg_popularity[symbol] = 0

    st.session_state["tg_popularity"] = tg_popularity

# ---------------------- DISPLAY FUNCTIONS ---------------------- #

def display_data(client):
    """Displays the surveillance dashboard data."""
    tracked_tokens = st.session_state["tracked_tokens"]
    market_cap_history = st.session_state["market_cap_history"]
    velocities = st.session_state["velocities"]
    tg_popularity = st.session_state["tg_popularity"]

    st.markdown("<h1>TOKEN SURVEILLANCE DASHBOARD</h1>", unsafe_allow_html=True)

    # Button to check Telegram Popularity (triggers authentication if not authenticated)
    if st.button("📈 Check Telegram Popularity"):
        if not st.session_state["authenticated"]:
            # Launch Tkinter authentication in a separate thread
            auth_thread = threading.Thread(target=launch_authentication_window)
            auth_thread.start()
            auth_thread.join()  # Wait for authentication to complete
            # Re-initialize the Telegram client after authentication
            if os.path.exists(SESSION_FILE):
                with open(SESSION_FILE, 'r') as f:
                    string_session = f.read().strip()
                # Initialize a new event loop in the main thread
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                client = TelegramClient(StringSession(string_session), TELEGRAM_API_ID, TELEGRAM_API_HASH)
                try:
                    client.connect()
                    if not client.is_user_authorized():
                        st.warning("Telegram client is not authorized. Please authenticate again.")
                        st.session_state["authenticated"] = False
                    else:
                        st.session_state["authenticated"] = True
                        logger.info("Telegram client re-initialized and authorized.")
                except Exception as e:
                    st.error(f"Error reconnecting Telegram client: {e}")
                    logger.error(f"Error reconnecting Telegram client: {e}")
                    st.session_state["authenticated"] = False
        else:
            # If already authenticated, proceed to scrape popularity
            if client:
                with st.spinner("Checking Telegram popularity..."):
                    scrape_and_update_popularity(client)
                st.success("Telegram popularity updated successfully!")
            else:
                st.warning("Telegram client is not initialized. Please authenticate first.")

    # Button to refresh dashboard data
    if st.button("🔄 Refresh Dashboard"):
        if st.session_state["authenticated"]:
            with st.spinner("Refreshing dashboard..."):
                update_data(client)
            st.success("Dashboard refreshed successfully!")
        else:
            st.warning("Please authenticate first by checking Telegram Popularity.")

    st.markdown("<h2>CURRENTLY TRACKED TOKENS</h2>", unsafe_allow_html=True)
    if tracked_tokens:
        df_tracked = pd.DataFrame([
            {
                "Name": d.get("name", "N/A"),
                "Symbol": d.get("symbol", "N/A"),
                "Mint": d.get("address", "N/A"),
                "Market Cap (SOL)": d.get("market_cap", 0.0),
                "Liquidity (USD)": d.get("liquidity_usd", 0.0),
                "Dev Holding (%)": f"{d.get('dev_holding_percent', 0.0):.2f}%",
                "Creator Wallet": d.get("creator_wallet", "Unknown"),
                "Telegram Popularity": tg_popularity.get(d.get("symbol", ""), 0)
            }
            for d in tracked_tokens.values()
        ])

        if df_tracked.empty:
            st.markdown("<p class='no-data'>No tokens currently tracked.</p>", unsafe_allow_html=True)
        else:
            styled_tracked = df_tracked.style.format({
                "Market Cap (SOL)": "{:,.2f} SOL",
                "Liquidity (USD)": "${:,.2f}",
                "Dev Holding (%)": "{}",
                "Telegram Popularity": "{}"
            }).background_gradient(subset=["Market Cap (SOL)", "Liquidity (USD)"], cmap="Blues")

            st.dataframe(styled_tracked, use_container_width=True)
    else:
        st.markdown("<p class='no-data'>No tokens currently tracked.</p>", unsafe_allow_html=True)

    # ---- Top Movers ----
    st.markdown("<h2>TOP MOVERS (HIGHEST VELOCITY)</h2>", unsafe_allow_html=True)
    if velocities:
        # Sort by velocity descending
        movers = sorted(velocities.items(), key=lambda x: x[1], reverse=True)
        top_movers = movers[:5]
        df_movers = pd.DataFrame([
            {
                "Symbol": sym,
                "Velocity (%)": f"{vel:+.2f}%"
            }
            for sym, vel in top_movers
        ])
        st.dataframe(df_movers, use_container_width=True)
    else:
        st.markdown("<p class='no-data'>No velocity data available.</p>", unsafe_allow_html=True)

    # ---- Filters for Tokens ----
    st.markdown("<h2>FILTER TOKENS</h2>", unsafe_allow_html=True)
    
    # First Row: Market Cap and Velocity
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        min_market_cap = st.number_input(
            "Min Market Cap (SOL)", 
            min_value=0.0, 
            value=0.0, 
            step=10.0, 
            key="min_market_cap_input_unique"
        )
    with col2:
        max_market_cap = st.number_input(
            "Max Market Cap (SOL)", 
            min_value=0.0, 
            value=400.0, 
            step=10.0, 
            key="max_market_cap_input_unique"
        )
    with col3:
        min_velocity = st.number_input(
            "Min Velocity (%)", 
            min_value=-100.0, 
            value=-100.0, 
            step=1.0, 
            key="min_velocity_input_unique"
        )
    with col4:
        max_velocity = st.number_input(
            "Max Velocity (%)", 
            min_value=-100.0, 
            value=1000.0,  # Set a reasonable default maximum
            step=1.0, 
            key="max_velocity_input_unique"
        )
    
    # Second Row: Dev Holding and Liquidity
    col5, col6, col7, col8 = st.columns(4)
    
    with col5:
        min_dev_holding = st.number_input(
            "Min Dev Holding (%)", 
            min_value=0.0, 
            value=0.0, 
            step=0.1, 
            key="min_dev_holding_input_unique"
        )
    with col6:
        max_dev_holding = st.number_input(
            "Max Dev Holding (%)", 
            min_value=0.0, 
            value=100.0,  # Set a reasonable default maximum
            step=0.1, 
            key="max_dev_holding_input_unique"
        )
    with col7:
        min_liquidity = st.number_input(
            "Min Liquidity (USD)", 
            min_value=0.0, 
            value=0.0, 
            step=1000.0,  # Adjust step as needed
            key="min_liquidity_input_unique"
        )
    with col8:
        max_liquidity = st.number_input(
            "Max Liquidity (USD)", 
            min_value=0.0, 
            value=1000000.0,  # Set a reasonable default maximum
            step=1000.0, 
            key="max_liquidity_input_unique"
        )
    
    # Third Row: Telegram Popularity Filters
    col9, col10 = st.columns(2)
    
    with col9:
        min_tg_popularity = st.number_input(
            "Min Telegram Popularity", 
            min_value=0, 
            value=0, 
            step=1, 
            key="min_tg_popularity_input_unique"
        )
    with col10:
        max_tg_popularity = st.number_input(
            "Max Telegram Popularity", 
            min_value=0, 
            value=10,  # Set a reasonable default maximum
            step=1, 
            key="max_tg_popularity_input_unique"
        )
    
    # ---- Apply Filters ----
    filtered_tokens = []
    for symbol, details in tracked_tokens.items():
        mc = details.get("market_cap", 0.0)  # In SOL
        vel = velocities.get(symbol, 0.0)  # Percentage
        dev_pct = details.get("dev_holding_percent", 0.0)
        liquidity = details.get("liquidity_usd", 0.0)
        popularity = tg_popularity.get(symbol, 0)
        name = details.get("name", "N/A")
        sym = details.get("symbol", "N/A")

        if (mc >= min_market_cap and
            mc <= max_market_cap and
            vel >= min_velocity and
            vel <= max_velocity and
            dev_pct >= min_dev_holding and
            dev_pct <= max_dev_holding and
            liquidity >= min_liquidity and
            liquidity <= max_liquidity and
            popularity >= min_tg_popularity and
            popularity <= max_tg_popularity):
            filtered_tokens.append({
                "Name": name,
                "Symbol": sym,
                "Mint": details["address"],
                "Market Cap (SOL)": f"{mc:,.2f} SOL",
                "Liquidity (USD)": f"${liquidity:,.2f}",
                "Dev Holding (%)": f"{dev_pct:.2f}%",
                "Telegram Popularity": popularity
            })

    # ---- Display Filtered Tokens ----
    st.markdown("<h2>FILTERED TOKENS</h2>", unsafe_allow_html=True)
    if filtered_tokens:
        df_filtered = pd.DataFrame(filtered_tokens)
        st.dataframe(df_filtered, use_container_width=True)
    else:
        st.markdown("<p class='no-data'>No tokens match the filter criteria.</p>", unsafe_allow_html=True)

    # ---- Market Cap History Graph ----
    st.markdown("<h2>MARKET CAP HISTORY (≥ 390 SOL)</h2>", unsafe_allow_html=True)
    if market_cap_history:
        # Filter tokens that exceed GRAPH_THRESHOLD
        valid_for_graph = [sym for sym, det in tracked_tokens.items() if det.get("market_cap", 0.0) >= GRAPH_THRESHOLD]
        if valid_for_graph:
            chosen_sym = st.selectbox("Select a token for history:", valid_for_graph, key="chart_select_unique")
            caps = market_cap_history.get(chosen_sym, [])
            if len(caps) > 1:
                data_plot = []
                slice_caps = caps[-HISTORY_LENGTH:]
                for i, c_val in enumerate(slice_caps, start=1):
                    data_plot.append({"Iteration": i, "Market Cap (SOL)": c_val, "Symbol": chosen_sym})
                df_hist = pd.DataFrame(data_plot)
                fig = px.line(df_hist, x="Iteration", y="Market Cap (SOL)", color="Symbol",
                              title=f"Market Cap Over Time: {chosen_sym}")
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#e0e0e0"),
                    title_font=dict(size=20, color='#c197ff'),
                    xaxis=dict(gridcolor="rgba(155,89,182,0.2)", zerolinecolor="rgba(155,89,182,0.2)"),
                    yaxis=dict(gridcolor="rgba(155,89,182,0.2)", zerolinecolor="rgba(155,89,182,0.2)"),
                )
                st.plotly_chart(fig, use_container_width=True, key=f"chart_{chosen_sym}_unique")
            else:
                st.markdown("<p class='no-data'>Not enough history for this token.</p>", unsafe_allow_html=True)
        else:
            st.markdown("<p class='no-data'>No tokens meet the threshold for the graph.</p>", unsafe_allow_html=True)
    else:
        st.markdown("<p class='no-data'>No tokens tracked, so no history available.</p>", unsafe_allow_html=True)

# ---------------------- MAIN FUNCTION ---------------------- #

def run_surveillance_dashboard():
    """Main function to run the Token Surveillance Dashboard."""
    # Initialize event loop in main thread
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Initialize Telegram client
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, 'r') as f:
            string_session = f.read().strip()
        client = TelegramClient(StringSession(string_session), TELEGRAM_API_ID, TELEGRAM_API_HASH)
        try:
            client.connect()
            if not client.is_user_authorized():
                st.warning("Telegram session exists but is not authorized. Please authenticate.")
                st.session_state["authenticated"] = False
            else:
                st.session_state["authenticated"] = True
                logger.info("Telegram client initialized and authorized.")
        except Exception as e:
            st.error(f"Error connecting Telegram client: {e}")
            logger.error(f"Error connecting Telegram client: {e}")
            st.session_state["authenticated"] = False
    else:
        client = None
        st.session_state["authenticated"] = False

    # Update data first, then display
    update_data(client)
    display_data(client)

# ---------------------- EXECUTE DASHBOARD ---------------------- #

# Do not execute surveillance.py directly; it should be imported by holding.py
# if __name__ == "__main__":
#     run_surveillance_dashboard()

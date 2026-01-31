import os
import datetime
import json
import requests
import threading
import time
import sys

# Try to load from .env file first
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Loaded environment variables from .env file")
except ImportError:
    print("⚠️  python-dotenv not installed, using system environment variables")
    pass

# Bot credentials and API keys
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
HA_URL = os.environ.get('HA_URL', 'http://192.168.0.149:8123')
HA_ACCESS_TOKEN = os.environ.get('HA_ACCESS_TOKEN', '')
OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')

# Permanent admin user ID
PERMANENT_ADMIN = 266952987128233985

WHITELIST_FILE = "whitelist.json"
CONFIG_FILE = "config.json"

# System time and location
SYSTEM_TIME = None
SYSTEM_DATE = None
LOCATION = None
CITY = None
COUNTRY = None
PUBLIC_IP = None

def validate_tokens():
    """Validate that all required tokens are set"""
    errors = []
    
    if not DISCORD_BOT_TOKEN:
        errors.append("❌ DISCORD_BOT_TOKEN is not set in environment variables or .env file")
    elif DISCORD_BOT_TOKEN == 'your_actual_discord_token_here' or 'YOUR_TOKEN' in DISCORD_BOT_TOKEN:
        errors.append("❌ DISCORD_BOT_TOKEN is still set to the default/placeholder value")
    
    if not HA_ACCESS_TOKEN:
        errors.append("⚠️  HA_ACCESS_TOKEN is not set (Home Assistant commands will not work)")
    
    return errors

def update_system_time_date():
    """Update system time and date"""
    global SYSTEM_TIME, SYSTEM_DATE
    now = datetime.datetime.now()
    SYSTEM_DATE = now.strftime("%Y-%m-%d")
    SYSTEM_TIME = now.strftime("%H:%M:%S")
    return SYSTEM_DATE, SYSTEM_TIME

def get_location_by_ip():
    """Get location from IP address"""
    global LOCATION, CITY, COUNTRY, PUBLIC_IP
    
    try:
        # Get public IP
        ip_response = requests.get('https://api.ipify.org?format=json', timeout=5)
        ip_data = ip_response.json()
        PUBLIC_IP = ip_data.get('ip')
        
        if PUBLIC_IP:
            # Get location info
            location_response = requests.get(f'http://ip-api.com/json/{PUBLIC_IP}', timeout=5)
            location_data = location_response.json()
            
            if location_data.get('status') == 'success':
                CITY = location_data.get('city', 'Unknown')
                COUNTRY = location_data.get('country', 'Unknown')
                LOCATION = f"{CITY}, {COUNTRY}"
            else:
                LOCATION = "Unknown"
                CITY = "Unknown"
                COUNTRY = "Unknown"
        else:
            LOCATION = "Unknown"
            CITY = "Unknown"
            COUNTRY = "Unknown"
            
    except Exception as e:
        print(f"⚠️  Error getting location: {e}")
        LOCATION = "Unknown"
        CITY = "Unknown"
        COUNTRY = "Unknown"
    
    return LOCATION, CITY, COUNTRY

def start_location_updater():
    """Start background location updater"""
    def update_loop():
        while True:
            try:
                update_system_time_date()
                get_location_by_ip()
            except Exception as e:
                print(f"⚠️  Error in location updater: {e}")
            time.sleep(300)  # Update every 5 minutes
    
    thread = threading.Thread(target=update_loop, daemon=True)
    thread.start()
    print("✅ Location updater started")

# Validate tokens on startup
token_errors = validate_tokens()
if token_errors:
    print("\n" + "="*50)
    print("TOKEN CONFIGURATION ISSUES:")
    for error in token_errors:
        print(error)
    print("="*50 + "\n")
    
    # If Discord token is invalid, exit
    if "DISCORD_BOT_TOKEN" in str(token_errors[0]):
        print("❌ Cannot start without a valid Discord token")
        print("\nTo fix this:")
        print("1. Get your bot token from: https://discord.com/developers/applications")
        print("2. Edit your .env file and replace 'your_actual_discord_token_here' with your real token")
        print("3. Run the bot again")
        sys.exit(1)

# Initialize the variables
update_system_time_date()
get_location_by_ip()
start_location_updater()

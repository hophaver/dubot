#!/usr/bin/env python3
import json
import os
import sys

def setup_home_assistant():
    """Setup Home Assistant integration"""
    print("🏠 Home Assistant Setup")
    print("=" * 50)
    
    # Check .env file
    env_file = '.env'
    if not os.path.exists(env_file):
        print("❌ No .env file found")
        create = input("Create .env file? (y/n): ")
        if create.lower() == 'y':
            ha_url = input("Home Assistant URL (e.g., http://192.168.1.100:8123): ").strip()
            ha_token = input("Home Assistant Long-Lived Access Token: ").strip()
            
            with open(env_file, 'a') as f:
                f.write(f"\n# Home Assistant\n")
                f.write(f"HA_URL={ha_url}\n")
                f.write(f"HA_ACCESS_TOKEN={ha_token}\n")
            
            print(f"✅ Added Home Assistant config to {env_file}")
            os.chmod(env_file, 0o600)
    else:
        print("✅ .env file found")

    # Create empty entity cache
    cache_file = "ha_entities_cache.json"
    if not os.path.exists(cache_file):
        with open(cache_file, 'w') as f:
            json.dump({}, f, indent=2)
        print(f"✅ Created empty entity cache: {cache_file}")

if __name__ == "__main__":
    setup_home_assistant()

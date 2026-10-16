import requests
import sys
import os
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://soapbeta.streamflow.ca/iplvandv/part-to-model-search"
BEARER_TOKEN = os.getenv("VV_API_TOKEN")
API_USER = os.getenv("VV_API_USER")
API_PASS = os.getenv("VV_API_PASS")


def get_models_for_part(part_number):
    """Return the list of models a part fits, as a list of dicts."""
    if not BEARER_TOKEN:
        print("Missing VV_API_TOKEN — check your .env file.")
        return []

    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "username": API_USER,
        "password": API_PASS,
        "partNumber": part_number,
    }
    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return []

    try:
        data = resp.json()
    except ValueError:
        print("Response was not JSON")
        return []

    if not isinstance(data, list):
        print(f"Unexpected response shape: {type(data)}")
        return []
    return data


def check_fit(part_number, model_number):
    """Does this part fit this model? Returns (bool, matched_record_or_None)."""
    models = get_models_for_part(part_number)
    target = model_number.strip().upper()
    for m in models:
        if m.get("modelNumber", "").upper() == target:
            return True, m
    return False, None


def close_matches(models, model_number, limit=8):
    """Find models sharing a core substring, ignoring optional prefixes."""
    target = model_number.strip().upper()
    # strip a leading Y/W prefix and take a core chunk for loose matching
    core = target.lstrip("YW")[:6]
    hits = [
        m["modelNumber"]
        for m in models
        if core and core in m.get("modelNumber", "").upper()
    ]
    return hits[:limit]


if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else "341241"
    model = sys.argv[2] if len(sys.argv) > 2 else None

    models = get_models_for_part(part)
    print(f"Part {part} fits {len(models)} models.")

    if not models:
        print("No fitment data for that part (or an error occurred).")
        sys.exit(0)

    if model:
        fits, record = check_fit(part, model)
        if fits:
            print(f"YES — part {part} fits model {model} ({record['manufacturer']}).")
        else:
            print(f"NO — part {part} does not list model {model}.")
            close = close_matches(models, model)
            if close:
                print(f"   Closest models in the list: {', '.join(close)}")
"""V&V API client for the appliance parts-fitment bot.

Two rules this module enforces:
  1. Failures raise VvApiError. They are never quietly turned into "no
     results", so the bot can tell "V&V is down" apart from "V&V has no
     data for that part" and never fills the gap with a guess.
  2. Only data V&V actually returned comes out of here. Nothing is invented.

Config comes from .env (never commit it):
  VV_API_USER, VV_API_PASS   the V&V login
  VV_API_TOKEN               Bearer token (part-to-model endpoint only)
Optional overrides, so a URL change is a one-line .env edit:
  VV_PARTSINFO_URL, VV_PART_TO_MODEL_URL
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_PARTSINFO_URL = "https://soap.streamflow.ca/vandvapi/GetPartsInfo"
DEFAULT_PART_TO_MODEL_URL = "https://soapbeta.streamflow.ca/iplvandv/part-to-model-search"
TIMEOUT_SECONDS = 15
MAX_WORKERS = 4  # parallel GetPartsInfo calls; kept low to be polite to V&V

# V&V rejects "*" as a wildcard mfgCode (retCode 211 "Invalid mfgCode"), so
# we guess from the part-number prefix first, then try the six primary codes.
PRIMARY_MFG_CODES = ["WHP", "GEN", "FRG", "LGC", "SAM", "SPQ"]


class VvApiError(Exception):
    """V&V could not be reached, or sent back something we can't use."""


class VvAuthError(VvApiError):
    """V&V rejected the login or the token."""


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise VvApiError(f"Missing {name} - check your .env file.")
    return value


def _post(url: str, body: dict, headers: dict | None = None):
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise VvApiError(f"Could not reach V&V: {e}") from e
    if resp.status_code in (401, 403):
        raise VvAuthError(f"V&V rejected the credentials (HTTP {resp.status_code}).")
    if resp.status_code != 200:
        raise VvApiError(f"V&V returned HTTP {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as e:
        raise VvApiError("V&V returned a response that is not JSON.") from e


# ---------------------------------------------------------------- part -> models

def part_to_models(part_number: str) -> list:
    """Models a part fits (beta endpoint). Empty list = V&V has no data.

    Raises VvApiError on any failure, including a response that isn't a
    list - the error text includes a short preview so we can see what V&V
    sends for a part with no model data.
    """
    token = _env("VV_API_TOKEN")
    body = {
        "username": _env("VV_API_USER"),
        "password": _env("VV_API_PASS"),
        "partNumber": part_number.strip(),
    }
    url = os.getenv("VV_PART_TO_MODEL_URL") or DEFAULT_PART_TO_MODEL_URL
    data = _post(url, body, {"Authorization": f"Bearer {token}"})
    if not isinstance(data, list):
        raise VvApiError(
            f"Unexpected part-to-model response ({type(data).__name__}): {str(data)[:200]}"
        )
    return data


# ---------------------------------------------------------------- GetPartsInfo

def guess_mfg_codes(part_number: str) -> list:
    """Manufacturer codes to try, best guess first, then the six primaries."""
    pn = part_number.strip().upper()
    guesses = []
    if re.match(r"^(WB|WD|WE|WG|WH|WJ|WR|WS|WX)\d", pn):
        guesses.append("GEN")
    elif re.match(r"^(WPW|WP|W)\d", pn):
        guesses.append("WHP")
    elif re.match(r"^(DA|DB|DC|DD|DE|DG|DJ)\d", pn):
        guesses.append("SAM")
    elif re.match(r"^(EBR|EAU|AGF|MCK|ADQ|AEB|ABQ|MEG|EBZ|AJU|MDS|ACQ|AAP)", pn):
        guesses.append("LGC")
    elif re.match(r"^(5304|316|318|134|137|241|242|297|807)", pn):
        guesses.append("FRG")
    return list(dict.fromkeys(guesses + PRIMARY_MFG_CODES))


def _call_parts_info(part_number: str, mfg_code: str):
    """One GetPartsInfo call. Returns the decoded response, or None if V&V
    doesn't recognise the part / code. GetPartsInfo needs no Bearer token."""
    body = {
        "commonHeader": {"user": _env("VV_API_USER"), "password": _env("VV_API_PASS")},
        "partNumber": part_number,
        "mfgCode": mfg_code,
    }
    url = os.getenv("VV_PARTSINFO_URL") or DEFAULT_PARTSINFO_URL
    data = _post(url, body)
    if not isinstance(data, dict):
        raise VvApiError("Unexpected GetPartsInfo response shape.")
    ret = data.get("return")
    code = str(ret.get("retCode", "")) if isinstance(ret, dict) else ""
    if code == "201":
        raise VvAuthError("V&V rejected the login (retCode 201).")
    if code != "200":
        return None
    return data


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    number = _to_float(value)
    return int(number) if number is not None else None


def _item(d: dict) -> dict:
    """Normalise one partData / subPartData entry. Deliberately skips V&V's
    flag fields (their types are inconsistent, e.g. oversize can be [] or "N")
    and the dealer cost fields - a customer-facing bot only needs list price."""
    locations = d.get("availableLocation")
    if not isinstance(locations, list):
        locations = []
    return {
        "partNumber": str(d.get("partNumber", "")),
        "mfgCode": str(d.get("mfgCode", "")),
        "description": str(d.get("partDescription", "")).strip(),
        "subbed": d.get("subbed") == "Y",
        "discontinued": d.get("discontinued") == "Y",
        "list_price": _to_float(d.get("retailPrice")),
        "quantity_available": _to_int(d.get("quantityOnHand")),
        "locations": [
            {"name": str(loc.get("locationName", "")), "quantity": _to_int(loc.get("availableQuantity"))}
            for loc in locations
            if isinstance(loc, dict)
        ],
    }


def normalize_part_info(raw: dict) -> dict:
    """V&V's response -> one flat dict. When a part is subbed, V&V puts NO
    price, stock or branches on the old number; they live on the entries in
    'substitutes' under the new number."""
    info = _item(raw.get("partData") or {})
    subs = raw.get("subPartData")
    info["substitutes"] = [_item(s) for s in subs if isinstance(s, dict)] if isinstance(subs, list) else []
    return info


def current_part(info: dict):
    """The part a customer should actually buy: the part itself, or its
    replacement if V&V subbed it. None if every substitute is itself subbed
    (a chain we refuse to guess our way through)."""
    if not info["subbed"]:
        return info
    for sub in info["substitutes"]:
        if not sub["subbed"]:
            return sub
    return None


def get_part_info(part_number: str, mfg_code: str | None = None):
    """Current info for one part, or None if V&V doesn't know it.

    With an mfg_code (e.g. from a diagram's parts list) only that code is
    tried. Without one, codes are guessed from the part number.
    """
    pn = part_number.strip().upper()
    given = (mfg_code or "").strip().upper()
    for code in ([given] if given else guess_mfg_codes(pn)):
        raw = _call_parts_info(pn, code)
        if raw is None:
            continue
        info = normalize_part_info(raw)
        if info["description"]:
            return info
    return None


def _lookup_one(key):
    pn, mfg = key
    try:
        info = get_part_info(pn, mfg)
    except VvAuthError:
        raise  # bad login: abort the whole batch instead of failing 15 times
    except VvApiError as e:
        return {"partNumber": pn, "status": "error", "error": str(e)}
    if info is None:
        return {"partNumber": pn, "status": "not_found"}
    return {"partNumber": pn, "status": "ok", "info": info}


def get_parts_info_many(items: list, max_workers: int = MAX_WORKERS) -> list:
    """Look up many parts in parallel (GetPartsInfo takes one part per call).

    items: part-number strings, or {"partNumber": ..., "mfgCode": ...} dicts.
    Returns one result per input, in input order:
      {"partNumber", "status": "ok",        "info": {...}}
      {"partNumber", "status": "not_found"}
      {"partNumber", "status": "error",     "error": "..."}
    The bot should only quote price/stock for status == "ok".
    """
    keys = []
    for item in items:
        if isinstance(item, str):
            pn, mfg = item, None
        else:
            pn, mfg = item.get("partNumber", ""), item.get("mfgCode")
        keys.append((pn.strip().upper(), (mfg or "").strip().upper() or None))
    unique = list(dict.fromkeys(keys))  # same part twice = one API call
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = dict(zip(unique, pool.map(_lookup_one, unique)))
    return [results[k] for k in keys]

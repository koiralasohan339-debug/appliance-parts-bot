"""Part <-> model fitment checks against V&V's beta part-to-model search.

Usage:
  python fitment.py                  # part 341241, lists how many models it fits
  python fitment.py 341241           # same, for another part
  python fitment.py 341241 MODEL123  # does this part fit this model?
"""
import sys

from vv_client import VvApiError, part_to_models


def check_fit(models, model_number):
    """Is the model in this part's model list? Returns (bool, record_or_None)."""
    target = model_number.strip().upper()
    for m in models:
        if str(m.get("modelNumber", "")).upper() == target:
            return True, m
    return False, None


def close_matches(models, model_number, limit=8):
    """Find models sharing a core substring, ignoring optional prefixes."""
    target = model_number.strip().upper()
    # strip a leading Y/W prefix and take a core chunk for loose matching
    core = target.lstrip("YW")[:6]
    hits = [
        str(m.get("modelNumber", ""))
        for m in models
        if core and core in str(m.get("modelNumber", "")).upper()
    ]
    return hits[:limit]


if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else "341241"
    model = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        models = part_to_models(part)  # one API call, reused below
    except VvApiError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Part {part} fits {len(models)} models.")

    if not models:
        print("V&V has no fitment data for that part.")
        sys.exit(0)

    if model:
        fits, record = check_fit(models, model)
        if fits:
            print(f"YES - part {part} fits model {model} ({record.get('manufacturer', 'unknown')}).")
        else:
            print(f"NO - part {part} does not list model {model}.")
            close = close_matches(models, model)
            if close:
                print(f"   Closest models in the list: {', '.join(close)}")

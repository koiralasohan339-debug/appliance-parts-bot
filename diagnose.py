import requests
import sys

def diagnose(symptom):
    """Query iFixit for a repair symptom and return the useful results."""
    url = f"https://www.ifixit.com/api/2.0/search/{symptom}"
    params = {"limit": 10}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return

    data = resp.json()
    results = data.get("results", [])

    if not results:
        print(f"No results for '{symptom}'.")
        return

    # Keep only troubleshooting wikis — the authored guides, not noisy Q&A
    wikis = [r for r in results if r.get("dataType") == "wiki"]

    print(f"\nSymptom searched: {symptom}")
    print(f"Total results: {len(results)}  |  Troubleshooting guides: {len(wikis)}")
    print("=" * 60)

    if not wikis:
        print("No structured troubleshooting guides — only community Q&A.")
        return

    for i, w in enumerate(wikis, 1):
        title = w.get("display_title") or w.get("title", "(no title)")
        summary = w.get("summary", "(no summary)")
        link = w.get("url", "")
        print(f"\n[{i}] {title}")
        print(f"    {summary}")
        print(f"    Source: {link}")

if __name__ == "__main__":
    # take the symptom from the command line, or ask for it
    if len(sys.argv) > 1:
        symptom = " ".join(sys.argv[1:])
    else:
        symptom = input("Describe the problem (e.g. 'dishwasher leaking'): ")
    diagnose(symptom)
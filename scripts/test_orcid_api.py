#!/usr/bin/env python3
"""
Test ORCID API access with your .secrets credentials.
Uses client-credentials (scope=/read-public). Run from project root:
  python3 scripts/test_orcid_api.py

ORCID docs: "Integrators using the member API can use the /read-public scope to
read ORCID record summaries." Public API clients can get a token but reading
record/works may be Member API only (403). Redirect URIs apply only to 3-legged OAuth.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ORCID_ID = "0000-0002-7551-4378"


def main() -> None:
    secrets_path = PROJECT_ROOT / ".secrets"
    if not secrets_path.exists():
        print("No .secrets file found")
        sys.exit(1)
    secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
    client_id = secrets.get("ORCID_CLIENT_ID") or secrets.get("orcid_client_id")
    client_secret = secrets.get("ORCID_CLIENT_SECRET") or secrets.get("orcid_client_secret")
    if not client_id or not client_secret:
        print("Missing ORCID_CLIENT_ID or ORCID_CLIENT_SECRET in .secrets")
        sys.exit(1)

    import requests

    token_url = "https://orcid.org/oauth/token"
    token_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "/read-public",
    }
    print("1. Requesting token (POST orcid.org/oauth/token, scope=/read-public)...")
    tr = requests.post(token_url, data=token_data, headers={"Accept": "application/json"}, timeout=15)
    print(f"   Token response: {tr.status_code}")
    if tr.status_code != 200:
        print(f"   Body: {tr.text[:500]}")
        sys.exit(1)
    token = tr.json().get("access_token")
    if not token:
        print("   No access_token in response")
        sys.exit(1)
    print(f"   Token received (first 20 chars): {token[:20]}...")

    record_url = f"https://api.orcid.org/v3.0/{ORCID_ID}/record"
    for accept_label, accept_header in [
        ("application/vnd.orcid+json", "application/vnd.orcid+json"),
        ("application/vnd.orcid+xml", "application/vnd.orcid+xml"),
    ]:
        headers = {"Authorization": f"Bearer {token}", "Accept": accept_header}
        print(f"2. Requesting record (GET .../record, Accept: {accept_label})...")
        rec = requests.get(record_url, headers=headers, timeout=15)
        print(f"   Response: {rec.status_code}")
        if rec.status_code == 200:
            if "json" in accept_label:
                data = rec.json()
                activities = data.get("activities-summary") or data.get("activities_summary") or {}
                works = activities.get("works") or {}
                groups = works.get("group") or works.get("groups") or []
                print(f"   OK. Work groups in record: {len(groups)}")
            else:
                print("   OK (XML response).")
            sys.exit(0)
        if rec.status_code == 403:
            print(f"   403 Forbidden: {rec.text.strip()[:400]}")
            continue
        print(f"   Body: {rec.text[:300]}")
        break

    print("\n   ORCID returned 403 (access_denied) for GET record.")
    print("   Docs: 'Integrators using the member API can use the /read-public scope to read")
    print("   ORCID record summaries.' So reading records may require Member API, not Public API.")
    print("   - Use --no-discovery in step 2 to skip ORCID/arXiv discovery.")
    print("   - Or use Member API credentials if your org is an ORCID member.")
    sys.exit(1)


if __name__ == "__main__":
    main()

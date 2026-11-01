"""Authorize locally, select a real property, and save private credentials.

pip install -r requirements-gsc.txt
python scripts/gsc_auth.py gsc_client.json --config config.json
python scripts/gsc_auth.py --check --config config.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import gsc


def private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".gsc-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def select_property(entries: list[dict], target: str, requested: str = "") -> str:
    choices = [s["siteUrl"] for s in entries if gsc.covers(s["siteUrl"], target)]
    if requested:
        if requested not in choices:
            raise RuntimeError("--site must exactly match an accessible property covering target_url")
        return requested
    if not choices:
        raise RuntimeError("No accessible property covers target_url; grant this Google account GSC access first")
    if len(choices) == 1:
        return choices[0]
    print("Accessible properties covering the target:")
    for i, site in enumerate(choices, 1):
        print(f"  {i}. {site}")
    if not sys.stdin.isatty():
        raise RuntimeError("Multiple matching properties; rerun with --site and an exact listed property")
    selection = int(input("Select property number: "))
    if not 1 <= selection <= len(choices):
        raise RuntimeError("Invalid property selection")
    return choices[selection - 1]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("client", nargs="?", default="gsc_client.json")
    p.add_argument("--config", default="config.json")
    p.add_argument("--target", default="https://meme.ransomz.dev/")
    p.add_argument("--site", default="")
    p.add_argument("--check", action="store_true", help="verify existing credentials; no new consent")
    args = p.parse_args(argv)
    path = Path(args.config).resolve()
    raw = json.loads(path.read_text()) if path.exists() else {"lang": "zh"}
    settings = raw.setdefault("gsc", {})
    target = settings.get("target_url", args.target)
    token_path = path.parent / settings.get("token_file", ".secrets/gsc_token.json")
    cfg = {"_dir": path.parent, "gsc": {**settings, "token_file": str(token_path)}}
    if args.check:
        access = gsc._token(cfg)
    else:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(args.client, [gsc.SCOPE], autogenerate_code_verifier=True)
        creds = flow.run_local_server(host="localhost", port=0, access_type="offline", prompt="consent")
        if not creds.refresh_token:
            raise RuntimeError("No refresh token returned; repeat offline consent")
        granted = creds.granted_scopes or creds.scopes
        if set(granted) != {gsc.SCOPE}:
            raise RuntimeError("Unexpected granted scopes; use a dedicated readonly OAuth client")
        access = creds.token
    entries = gsc.sites(access)
    site = select_property(entries, target, args.site or settings.get("site", ""))
    if args.check:
        print(f"Verified read-only access: {site}; target: {target}")
        return 0
    # Persist only after property selection succeeds. No tokens on stdout.
    private_write(token_path, creds.to_json())
    settings.update(site=site, target_url=target, token_file=os.path.relpath(token_path, path.parent), fresh=False)
    private_write(path, json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
    print(f"Verified property: {site}")
    print(f"Saved private token and config. Next: python -m radar.gsc --config {args.config} --check")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"GSC authorization failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

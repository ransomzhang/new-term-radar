"""Read-only GSC discovery. New means absent from returned baseline rows, not all searches.

Standalone: python -m radar.gsc --config config.json --check
             python -m radar.gsc --config config.json --out gsc-report.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import UA

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/webmasters/v3/sites"
LAG_DAYS = 3  # default to finalized data; fresh mode is opt-in
RECENT, BEFORE = 3, 60
MIN_IMPRESSIONS, ROW_LIMIT = 5, 25000
_OPERATOR = re.compile(r'"|\b(site|inurl|intitle|intext|filetype):|(^|\s)-\w|\*|\bOR\b')


def _request(url: str, access: str = "", body: dict | None = None) -> dict:
    headers = {"User-Agent": UA}
    if access:
        headers["Authorization"] = f"Bearer {access}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        # Never echo response bodies or tokens to Actions logs.
        raise RuntimeError(f"GSC API HTTP {exc.code}; check property access and API enablement") from None


def _token(cfg: dict) -> str:
    path = cfg["_dir"] / cfg["gsc"].get("token_file", "gsc_token.json")
    raw = path.read_text() if path.exists() else os.environ.get("GSC_TOKEN_JSON", "")
    if not raw:
        raise RuntimeError("GSC credentials missing: run scripts/gsc_auth.py locally or set GSC_TOKEN_JSON")
    try:
        tok = json.loads(raw)
        scopes = tok.get("scopes", [])
        scopes = scopes.split() if isinstance(scopes, str) else scopes
        if set(scopes) != {SCOPE}:
            raise ValueError("scope")
        body = urllib.parse.urlencode({"client_id": tok["client_id"], "client_secret": tok["client_secret"],
                                       "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}).encode()
    except (KeyError, ValueError, TypeError):
        raise RuntimeError("Invalid token or scope; reauthorize with webmasters.readonly only") from None
    req = urllib.request.Request(TOKEN_URL, data=body, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)["access_token"]
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GSC token refresh HTTP {exc.code}; reauthorize locally") from None


def sites(access: str) -> list[dict]:
    return [s for s in _request(API, access).get("siteEntry", [])
            if s.get("permissionLevel") in {"siteOwner", "siteFullUser", "siteRestrictedUser"}]


def covers(site: str, target: str) -> bool:
    """Does this exact API property cover the target URL?"""
    url = urllib.parse.urlsplit(target)
    if url.scheme not in {"http", "https"} or not url.hostname:
        return False
    if site.startswith("sc-domain:"):
        domain = site[len("sc-domain:"):].lower()
        return url.hostname.lower() == domain or url.hostname.lower().endswith("." + domain)
    prop = urllib.parse.urlsplit(site)
    return (url.scheme, url.netloc.lower()) == (prop.scheme, prop.netloc.lower()) and url.path.startswith(prop.path)


def verify(access: str, site: str, target: str = "") -> dict:
    matches = [s for s in sites(access) if s["siteUrl"] == site]
    if not matches:
        raise RuntimeError("Configured GSC property is not accessible; list properties with gsc_auth.py --check")
    if target and not covers(site, target):
        raise RuntimeError("Configured GSC property does not cover target_url")
    return matches[0]


def _query(access: str, site: str, start: date, end: date, dimensions: list[str], fresh: bool,
           target: str = "") -> list[dict]:
    body = {"startDate": str(start), "endDate": str(end), "dimensions": dimensions,
            "rowLimit": ROW_LIMIT, "type": "web", "dataState": "all" if fresh else "final"}
    if target:
        parsed = urllib.parse.urlsplit(target)
        # Scope parent domain properties to this exact origin, not sibling sites.
        origin = f"{parsed.scheme}://{parsed.netloc}/"
        body["dimensionFilterGroups"] = [{"filters": [{"dimension": "page", "operator": "includingRegex",
                                                        "expression": "^" + re.escape(origin)}]}]
    url = f"{API}/{urllib.parse.quote(site, safe='')}/searchAnalytics/query"
    result = []
    offset = 0
    while True:
        response = _request(url, access, {**body, "startRow": offset})
        rows = response.get("rows", [])
        for row in rows:
            keys = dict(zip(dimensions, row["keys"]))
            if _OPERATOR.search(keys.get("query", "")):
                continue
            result.append({**keys, "impressions": int(row["impressions"]), "clicks": int(row["clicks"]),
                           "position": row.get("position")})
        if len(rows) < ROW_LIMIT:
            return result
        offset += len(rows)


def fresh_queries(recent: list[dict], before: list[dict]) -> list[dict]:
    seen = {r["query"] for r in before}
    grouped = {}
    for r in recent:
        if r["query"] in seen:
            continue
        f = grouped.setdefault(r["query"], {"query": r["query"], "impressions": 0, "clicks": 0,
                                            "pages": {}, "daily": {}})
        f["impressions"] += r["impressions"]
        f["clicks"] += r["clicks"]
        p = f["pages"].setdefault(r["page"], {"page": r["page"], "impressions": 0, "clicks": 0})
        p["impressions"] += r["impressions"]
        p["clicks"] += r["clicks"]
        if r.get("date"):
            f["daily"][r["date"]] = f["daily"].get(r["date"], 0) + r["impressions"]
    for f in grouped.values():
        f["pages"] = sorted(f["pages"].values(), key=lambda p: (-p["impressions"], p["page"]))
        f["page"] = f["pages"][0]["page"]  # existing report compatibility
        f["daily"] = dict(sorted(f["daily"].items()))
    return sorted(grouped.values(), key=lambda f: (-f["impressions"], f["query"]))


def gsc_new(cfg: dict) -> dict:
    settings = cfg["gsc"]
    site = settings.get("site")
    if not site:
        return {"off": True}
    access = _token(cfg)
    target = settings.get("target_url", "")
    property_info = verify(access, site, target)
    fresh_mode = settings.get("fresh", False)
    end = datetime.now(ZoneInfo("America/Los_Angeles")).date() - timedelta(days=1 if fresh_mode else LAG_DAYS)
    start = end - timedelta(days=RECENT - 1)
    before_start, before_end = start - timedelta(days=BEFORE), start - timedelta(days=1)
    recent = _query(access, site, start, end, ["query", "page", "date"], fresh_mode, target)
    before = _query(access, site, before_start, before_end, ["query"], False, target)
    found = fresh_queries(recent, before)
    qualified = [f for f in found if f["impressions"] >= MIN_IMPRESSIONS]
    return {"site": site, "property": property_info, "target_url": target, "window": f"{start}..{end}",
            "baseline_window": f"{before_start}..{before_end}", "timezone": "America/Los_Angeles",
            "data_state": "all (provisional)" if fresh_mode else "final",
            "total_new": len(found), "qualified_new": len(qualified), "min_impressions": MIN_IMPRESSIONS,
            "baseline_rows": len(before), "recent_rows": len(recent), "queries": qualified,
            "limitations": ["Absent from returned baseline rows only; anonymized and omitted queries are unavailable.",
                            "API returns top rows, not guaranteed exhaustive, even with pagination.",
                            "Page/day impressions are summed; these are not market search volume.",
                            "Three days is an early signal, not evidence of sustained growth.",
                            "Landing pages show observed coverage only; intent and SERP competition need review."]}


def main(argv=None) -> int:
    from .config import load
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config.json")
    p.add_argument("--check", action="store_true")
    p.add_argument("--out", default="gsc-report.json")
    args = p.parse_args(argv)
    cfg = load(Path(args.config))
    try:
        if not cfg["gsc"].get("site"):
            raise RuntimeError("No GSC site configured; authorize and select an actual property first")
        if args.check:
            print(json.dumps(verify(_token(cfg), cfg["gsc"]["site"], cfg["gsc"].get("target_url", "")), ensure_ascii=False))
        else:
            result = gsc_new(cfg)
            Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"Wrote {args.out}: {result['qualified_new']} candidates; {result['data_state']}")
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"GSC failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Pixiv batch downloader via aria2c RPC.

Usage: python3 pixiv-dl.py <csv> [--base-dir DIR] [--rpc URL] [--secret S] [--dry-run]

Sends download tasks to a running aria2c daemon via JSON-RPC.
"""

import argparse
import csv
import json
import os
import sys
import urllib.request
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description="Batch download pixiv images via aria2c RPC.")
    p.add_argument("csv", help="Path to the CSV file.")
    p.add_argument("--base-dir", default=str(Path.home()),
                   help="Base directory for relative paths [default: ~/]")
    p.add_argument("--rpc", default="http://localhost:6800/jsonrpc",
                   help="aria2c RPC endpoint")
    p.add_argument("--secret", default="",
                   help="aria2c RPC secret token")
    p.add_argument("--batch-size", type=int, default=50,
                   help="Tasks per multicall batch [default: 50]")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse CSV and report count without sending.")
    return p.parse_args()


def rpc_call(endpoint, method, params):
    """Single JSON-RPC call."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": "pixdl",
        "method": method,
        "params": params,
    }).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def rpc_multicall(endpoint, calls):
    """system.multicall — batch multiple aria2.addUri calls."""
    mc_params = []
    for method, params in calls:
        mc_params.append({"methodName": method, "params": params})
    return rpc_call(endpoint, "system.multicall", [mc_params])


def main():
    args = parse_args()
    csv_path = Path(args.csv).expanduser().resolve()
    base_dir = Path(args.base_dir).expanduser().resolve()
    endpoint = args.rpc

    if not csv_path.is_file():
        print(f"[ERROR] CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    # Detect encoding
    enc = "utf-8"
    for try_enc in ["utf-8-sig", "utf-8", "gbk", "shift_jis"]:
        try:
            with open(csv_path, "r", encoding=try_enc) as f:
                f.read(256)
            enc = try_enc
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    entries = []
    with open(csv_path, "r", encoding=enc, newline="") as f:
        reader = csv.DictReader(f)
        if "original" not in reader.fieldnames or "fileName" not in reader.fieldnames:
            print("[ERROR] CSV needs 'original' and 'fileName' columns.", file=sys.stderr)
            sys.exit(1)
        for row in reader:
            url = row["original"].strip()
            fname = row["fileName"].strip()
            if url and fname:
                entries.append((url, fname))

    if not entries:
        print("[WARN] No entries found.", file=sys.stderr)
        sys.exit(0)

    # Filter already-downloaded
    to_dl = []
    skipped = 0
    for url, fname in entries:
        save_path = base_dir / fname
        if save_path.exists():
            skipped += 1
        else:
            to_dl.append((url, fname))

    print(f"[INFO] {len(entries)} total, {skipped} exist, {len(to_dl)} to download.")

    if not to_dl:
        print("[INFO] All files exist. Done.")
        return

    if args.dry_run:
        print("[DRY-RUN] Would send {} tasks to {}".format(len(to_dl), endpoint))
        return

    # Test RPC connectivity
    try:
        ver = rpc_call(endpoint, "aria2.getVersion", _token_params(args.secret))
        print("[INFO] aria2c version: " + ver.get("result", {}).get("version", "?"))
    except Exception as e:
        print(f"[ERROR] Cannot reach aria2c RPC at {endpoint}: {e}", file=sys.stderr)
        sys.exit(1)

    # Build and send batches
    token_prefix = _token_params(args.secret)
    sent = 0
    batch = []
    for url, fname in to_dl:
        save_path = base_dir / fname
        parent = str(save_path.parent)
        name = save_path.name
        opts = {
            "dir": parent,
            "out": name,
            "header": ["Referer: https://www.pixiv.net/"],
            "auto-file-renaming": "false",
            "continue": "true",
            "split": "4",
            "max-connection-per-server": "4",
        }
        params = token_prefix + [[url], opts]
        batch.append(("aria2.addUri", params))

        if len(batch) >= args.batch_size:
            rpc_multicall(endpoint, batch)
            sent += len(batch)
            print(f"\r[PROGRESS] {sent}/{len(to_dl)}", end="", flush=True)
            batch = []

    if batch:
        rpc_multicall(endpoint, batch)
        sent += len(batch)

    print(f"\n[DONE] {sent} tasks sent to aria2c.")


def _token_params(secret):
    """Return token prefix list for RPC params."""
    if secret:
        return ["token:" + secret]
    return []


if __name__ == "__main__":
    main()

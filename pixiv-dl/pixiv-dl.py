#!/usr/bin/env python3
"""Pixiv batch downloader via aria2c RPC.

Supports CSV and JSON input formats.
- CSV: Uses 'original' and 'fileName' columns. Ugoira uses default FPS with warning.
- JSON: Uses full metadata including ugoiraInfo for precise frame delays.

Ugoira (animated illustrations) are downloaded as ZIP, extracted, and converted
to WebM via ffmpeg using the concat demuxer.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(
        description="Batch download pixiv images via aria2c RPC."
    )
    p.add_argument("input",
                   help="Path to CSV or JSON file (auto-detected by extension).")
    p.add_argument("--base-dir", default=str(Path.home()),
                   help="Base directory for relative paths [default: ~/]")
    p.add_argument("--rpc", default="http://localhost:6800/jsonrpc",
                   help="aria2c RPC endpoint")
    p.add_argument("--secret", default="",
                   help="aria2c RPC secret token")
    p.add_argument("--batch-size", type=int, default=50,
                   help="Tasks per multicall batch [default: 50]")
    p.add_argument("--max-fn-bytes", type=int, default=250,
                   help="Max filename byte length [default: 250]")
    p.add_argument("--ugoira-fps", type=int, default=20,
                   help="Default FPS when frame delays unavailable [default: 20]")
    p.add_argument("--ugoira-jobs", type=int, default=4,
                   help="Parallel ugoira conversion threads [default: 4]")
    p.add_argument("--ugoira-quality", default="lossless",
                   help="WebM quality: 'lossless' (default) or CRF 0-63")
    p.add_argument("--ugoira-only", action="store_true",
                   help="Only process ugoira (skip normal images).")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse input and report counts without downloading.")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def _sanitize_path(fname):
    """Replace '/' inside the filename component with fullwidth '／'.
    
    Assumes directory depth <= 2 (e.g. pixiv/{category}/{filename}).
    """
    parts = fname.split("/", 2)
    if len(parts) == 3:
        parts[2] = parts[2].replace("/", "／")
    return "/".join(parts)


def truncate_filename(filename, max_bytes=250):
    """Truncate filename to fit within *max_bytes* UTF-8, keeping extension."""
    if len(filename.encode("utf-8")) <= max_bytes:
        return filename, False
    stem, ext = os.path.splitext(filename)
    target = max_bytes - len(ext.encode("utf-8"))
    while stem and len(stem.encode("utf-8")) > target:
        stem = stem[:-1]
    return stem + ext, True


def _make_fname_from_json(item):
    """Build a save path from JSON metadata (mirrors CSV fileName format)."""
    xr = item.get("xRestrict", 0)
    rdir = {0: "All ages", 1: "R-18", 2: "R-18G"}.get(xr, "Other")
    iid = str(item.get("id", item.get("idNum", "unknown")))
    user = item.get("user", "unknown").replace("/", "／")
    tags = ",".join(item.get("tags", [])).replace("/", "／")
    ext = item.get("ext", "jpg")
    return f"pixiv/{rdir}/{iid}_{user}_{tags}.{ext}"


# ---------------------------------------------------------------------------
# aria2c RPC
# ---------------------------------------------------------------------------

def _rpc(endpoint, method, params):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": "pixdl", "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        endpoint, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _rpc_multi(endpoint, calls):
    mc = [{"methodName": m, "params": p} for m, p in calls]
    return _rpc(endpoint, "system.multicall", [mc])


def _tok(secret):
    return ["token:" + secret] if secret else []


# ---------------------------------------------------------------------------
# Ugoira processing
# ---------------------------------------------------------------------------

def _process_ugoira(url, save_path, frame_delays=None, default_fps=20, quality="lossless"):
    """Download ugoira ZIP -> extract frames -> ffmpeg concat -> WebM."""
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "ugoira.zip")
        req = urllib.request.Request(
            url, headers={"Referer": "https://www.pixiv.net/"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                with open(zip_path, "wb") as zf:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        zf.write(chunk)
        except Exception as e:
            print(f"\n  [ERROR] download: {e}", file=sys.stderr)
            return False

        frames_dir = os.path.join(tmpdir, "frames")
        os.makedirs(frames_dir)
        try:
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(frames_dir)
        except zipfile.BadZipFile as e:
            print(f"\n  [ERROR] bad zip: {e}", file=sys.stderr)
            return False

        frames = sorted(f for f in os.listdir(frames_dir) if not f.startswith("."))
        if not frames:
            print("\n  [ERROR] empty zip", file=sys.stderr)
            return False

        # Build concat demuxer input
        concat_path = os.path.join(tmpdir, "concat.txt")
        with open(concat_path, "w") as cf:
            for i, frame in enumerate(frames):
                fpath = os.path.join(frames_dir, frame).replace("'", r"'\''")
                if frame_delays and i < len(frame_delays):
                    dur = frame_delays[i] / 1000.0
                else:
                    dur = 1.0 / default_fps
                cf.write(f"file '{fpath}'\nduration {dur:.6f}\n")
            last = os.path.join(frames_dir, frames[-1]).replace("'", r"'\''")
            cf.write(f"file '{last}'\n")

        ext = os.path.splitext(frames[0])[1].lower()
        pix_fmt = "yuva420p" if ext == ".png" else "yuv420p"

        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-c:v", "libvpx-vp9",
        ]
        if quality == "lossless":
            cmd += ["-lossless", "1"]
        else:
            cmd += ["-crf", str(quality), "-b:v", "0"]
        cmd += ["-pix_fmt", pix_fmt, "-an", save_path]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                print(f"\n  [ERROR] ffmpeg: {(r.stderr or '')[-200:]}", file=sys.stderr)
                return False
        except subprocess.TimeoutExpired:
            print("\n  [ERROR] ffmpeg timeout", file=sys.stderr)
            return False

    return True


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _parse_csv(path, max_fn):
    """Return list of entry dicts from CSV."""
    enc = "utf-8"
    for te in ("utf-8-sig", "utf-8", "gbk", "shift_jis"):
        try:
            with open(path, "r", encoding=te) as f:
                f.read(256)
            enc = te
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    entries, trunc = [], 0
    warned = False
    with open(path, "r", encoding=enc, newline="") as f:
        rd = csv.DictReader(f)
        flds = rd.fieldnames or []
        if "original" not in flds or "fileName" not in flds:
            print("[ERROR] CSV needs 'original' and 'fileName' columns.", file=sys.stderr)
            sys.exit(1)
        has_type = "type" in flds

        for row in rd:
            url = row["original"].strip()
            fname = row["fileName"].strip()
            if not url or not fname:
                continue

            # Sanitize '/' in filename (e.g. Fate/StayNight in tags)
            fname = _sanitize_path(fname)

            if "/" in fname:
                dpart, npart = fname.rsplit("/", 1)
            else:
                dpart, npart = "", fname
            npart, wt = truncate_filename(npart, max_fn)
            if wt:
                trunc += 1
                fname = f"{dpart}/{npart}" if dpart else npart
                print(f"[WARN] Truncated: ...{fname[-50:]}")

            is_ugo = (has_type and row.get("type", "").lower() == "ugoira") or "ugoira" in url.lower()
            if is_ugo and not warned:
                print("[WARN] CSV mode: ugoira frame delays unavailable — "
                      "using default FPS. Use JSON input for precise timing.")
                warned = True

            entries.append({"url": url, "fname": fname,
                            "is_ugoira": is_ugo, "frame_delays": None})

    if trunc:
        print(f"[INFO] {trunc} filename(s) truncated.")
    return entries


def _parse_json(path, max_fn):
    """Return list of entry dicts from JSON."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries, trunc = [], 0
    for item in data:
        url = (item.get("original") or "").strip()
        if not url:
            continue

        fname = _make_fname_from_json(item)
        if "/" in fname:
            dpart, npart = fname.rsplit("/", 1)
        else:
            dpart, npart = "", fname
        npart, wt = truncate_filename(npart, max_fn)
        if wt:
            trunc += 1
            fname = f"{dpart}/{npart}" if dpart else npart
            print(f"[WARN] Truncated: ...{fname[-50:]}")

        uinfo = item.get("ugoiraInfo")
        is_ugo = uinfo is not None or "ugoira" in url.lower()
        delays = None
        if uinfo and "frames" in uinfo:
            delays = [fr["delay"] for fr in uinfo["frames"]]

        entries.append({"url": url, "fname": fname,
                        "is_ugoira": is_ugo, "frame_delays": delays})

    if trunc:
        print(f"[INFO] {trunc} filename(s) truncated.")
    return entries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    inp = Path(args.input).expanduser().resolve()
    base = Path(args.base_dir).expanduser().resolve()

    if not inp.is_file():
        print(f"[ERROR] Not found: {inp}", file=sys.stderr)
        sys.exit(1)

    ext = inp.suffix.lower()
    if ext == ".json":
        entries = _parse_json(str(inp), args.max_fn_bytes)
    elif ext == ".csv":
        entries = _parse_csv(str(inp), args.max_fn_bytes)
    else:
        print(f"[ERROR] Unsupported format: {ext}", file=sys.stderr)
        sys.exit(1)

    # Filter existing
    norm_dl, ugo_dl = [], []
    skipped = 0
    for e in entries:
        if (base / e["fname"]).exists():
            skipped += 1
        elif e["is_ugoira"]:
            ugo_dl.append(e)
        elif not args.ugoira_only:
            norm_dl.append(e)

    ugo_total = sum(1 for e in entries if e["is_ugoira"])
    print(f"[INFO] {len(entries)} total ({ugo_total} ugoira), "
          f"{skipped} exist, {len(norm_dl)} normal + {len(ugo_dl)} ugoira to download.")

    if not norm_dl and not ugo_dl:
        print("[INFO] Nothing to download.")
        return
    if args.dry_run:
        print(f"[DRY-RUN] {len(norm_dl)} aria2c + {len(ugo_dl)} ugoira.")
        return

    # ---- aria2c ----
    if norm_dl:
        try:
            ver = _rpc(args.rpc, "aria2.getVersion", _tok(args.secret))
            print("[INFO] aria2c " + ver.get("result", {}).get("version", "?"))
        except Exception as e:
            print(f"[ERROR] aria2c unreachable: {e}", file=sys.stderr)
            sys.exit(1)

        tok = _tok(args.secret)
        sent, batch = 0, []
        for e in norm_dl:
            sp = base / e["fname"]
            opts = {
                "dir": str(sp.parent), "out": sp.name,
                "header": ["Referer: https://www.pixiv.net/"],
                "auto-file-renaming": "false",
                "continue": "true",
                "split": "4", "max-connection-per-server": "4",
            }
            batch.append(("aria2.addUri", tok + [[e["url"]], opts]))
            if len(batch) >= args.batch_size:
                _rpc_multi(args.rpc, batch)
                sent += len(batch)
                print(f"\r[PROGRESS] aria2c: {sent}/{len(norm_dl)}", end="", flush=True)
                batch = []
        if batch:
            _rpc_multi(args.rpc, batch)
            sent += len(batch)
        print(f"\n[DONE] {sent} tasks sent to aria2c.")

    # ---- ugoira ----
    if ugo_dl:
        jobs = args.ugoira_jobs
        quality = args.ugoira_quality
        print(f"[INFO] Processing {len(ugo_dl)} ugoira "
              f"({jobs} threads, quality={quality}) ...")
        ok = fail = 0

        def _do_one(idx, entry):
            sp = base / entry["fname"]
            return idx, entry, sp, _process_ugoira(
                entry["url"], str(sp), entry["frame_delays"],
                args.ugoira_fps, quality,
            )

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futs = {pool.submit(_do_one, i, e): i
                    for i, e in enumerate(ugo_dl, 1)}
            for fut in as_completed(futs):
                idx, entry, sp, success = fut.result()
                label = sp.name[:50] + ("..." if len(sp.name) > 50 else "")
                precise = entry["frame_delays"] is not None
                tag = "precise" if precise else f"{args.ugoira_fps}fps"
                status = "ok" if success else "FAIL"
                print(f"  [{idx}/{len(ugo_dl)}] {label} ({tag}) {status}")
                if success:
                    ok += 1
                else:
                    fail += 1
        print(f"[DONE] Ugoira: {ok} ok, {fail} failed.")


if __name__ == "__main__":
    main()

# pixiv-dl

Batch download Pixiv images from CSV or JSON metadata via aria2c RPC.

## Usage

```bash
python3 pixiv-dl.py [input_path] [--base-dir DIR] [--rpc URL] [--secret S] [--proxy URI] [--stop-aria2] [--batch-size N] [--dry-run]
```

## Arguments

| Arg | Default | Description |
|---|---|---|
| `input` | *(optional with `--stop-aria2`)* | CSV or JSON file (auto-detected by extension) |
| `--base-dir` | `~/` | Base directory for relative output paths |
| `--rpc` | `http://localhost:6800/jsonrpc` | aria2c JSON-RPC endpoint |
| `--secret` | *(none)* | aria2c RPC secret token |
| `--proxy` | *(none)* | Proxy for Pixiv resource downloads. Supports `http://`, `https://`, `socks4://`, `socks4a://`, `socks5://`, `socks5h://` |
| `--stop-aria2` | `false` | Stop and clear all aria2 active/waiting/stopped tasks before exiting or downloading |
| `--batch-size` | `50` | Tasks per multicall batch |
| `--max-fn-bytes` | `250` | Max UTF-8 byte length of final filename on non-Windows systems |
| `--ugoira-fps` | `20` | Fallback FPS for CSV ugoira downloads without frame delays |
| `--ugoira-jobs` | `4` | Parallel ugoira conversion threads |
| `--ugoira-quality` | `lossless` | WebM quality: `lossless` or CRF `0-63` |
| `--ugoira-only` | `false` | Only process ugoira entries |
| `--dry-run` | `false` | Parse input and report counts without downloading |

## Features

- Sends normal image downloads to a running aria2c daemon via JSON-RPC multicall
- Can stop and clear all aria2 tasks/results via `--stop-aria2`
- Auto-skips already-downloaded files
- Derives a per-artwork Pixiv `Referer` (`https://www.pixiv.net/artworks/<id>`) to avoid Pixiv CDN 403 on direct resource URLs
- Supports download proxies for both aria2c tasks and local ugoira ZIP fetching
- Sanitizes unsafe filename characters across path components, including Windows reserved names, slash-like Unicode characters, and JSON user/tag text before filename assembly so aria2 does not misinterpret them as subdirectories
- On Windows, disables script-side filename truncation and enables long-path handling for local file operations
- Auto-detects CSV encoding (UTF-8 BOM, GBK, Shift_JIS)
- Converts ugoira ZIPs to WebM with ffmpeg using relative concat paths to avoid Windows ffmpeg path issues, and saves converted files with a `.webm` suffix
- No Python package dependencies (uses stdlib; `curl` is only needed for proxied local ugoira downloads)

## Input Formats

### CSV

Requires at least these columns:

- `original`: Direct URL to the Pixiv image resource
- `fileName`: Relative save path (relative to `--base-dir`)

Optional:

- `type`: If set to `ugoira`, the row is processed as ugoira
- `artworkId`: Used as a Referer fallback if the script cannot infer artwork ID from URL

### JSON

Uses full Pixiv metadata. If `ugoiraInfo.frames` is present, frame delays are preserved for precise timing.

## Examples

```bash
python3 pixiv-dl.py --stop-aria2
python3 pixiv-dl.py export.csv --base-dir ~/Downloads
python3 pixiv-dl.py export.json --proxy socks5h://127.0.0.1:1080
python3 pixiv-dl.py export.csv --stop-aria2 --rpc http://localhost:6800/jsonrpc --secret mytoken
```

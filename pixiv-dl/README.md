# pixiv-dl

Batch download pixiv images from a CSV export via aria2c RPC.

## Usage

```bash
python3 pixiv-dl.py <csv_path> [--base-dir DIR] [--rpc URL] [--secret S] [--batch-size N] [--dry-run]
```

## Arguments

| Arg | Default | Description |
|---|---|---|
| `csv` | *(required)* | CSV file with `original` and `fileName` columns |
| `--base-dir` | `~/` | Base directory for relative `fileName` paths |
| `--rpc` | `http://localhost:6800/jsonrpc` | aria2c JSON-RPC endpoint |
| `--secret` | *(none)* | aria2c RPC secret token |
| `--batch-size` | `50` | Tasks per multicall batch |
| `--dry-run` | `false` | Parse CSV and report count without sending |

## Features

- Sends downloads to a running aria2c daemon via JSON-RPC multicall
- Auto-skips already-downloaded files
- Sets `Referer: https://www.pixiv.net/` header for CDN access
- Auto-detects CSV encoding (UTF-8 BOM, GBK, Shift_JIS)
- No external dependencies (stdlib only)

## CSV Format

Expects a CSV with at least these columns:
- `original`: Direct URL to the image
- `fileName`: Relative save path (relative to `--base-dir`)

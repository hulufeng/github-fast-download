---
name: github-fast-download
description: This skill should be used when a GitHub (or other) file download is slow — for example a user provides a release-asset signed URL (release-assets.githubusercontent.com), complains about low download speed, asks for "Xunlei/Thunder-style" multi-threaded or multi-source acceleration, or wants an open-source download solution. It diagnoses whether the file has one real source or many, then applies single-source multi-connection parallel chunk downloading (Python, resumable 1MB sub-packets) or true multi-source aria2, and documents the environment pitfalls that break naive approaches.
agent_created: true
---

# GitHub Fast Download

## Overview

Accelerate painfully slow downloads of large files (especially GitHub release
assets) by first diagnosing the source topology, then applying the matching
strategy. The core reusable asset is a battle-tested Python 64-thread chunk
downloader that defeats per-connection throttling with resumable 1MB
sub-packets and survives token expiry by reading the live URL each packet.

## When to use

- A user hands over a slow GitHub download link (often a signed
  `release-assets.githubusercontent.com/...` URL) and asks to "just download it".
- The user complains a download is "too slow" and mentions 迅雷/Xunlei,
  multi-thread, multi-source, or "open-source download tool".
- The user wants a reusable method/skill/agent for accelerating downloads.

## Workflow

### Step 1 — Diagnose source topology

Determine whether the file has **one real source** or **many**:

- Run `curl -IL <download-page-url>` and follow `location:` hops.
- If every mirror/proxy (ghproxy.net, etc.) 302-redirects back to the **same**
  host (e.g. `release-assets.githubusercontent.com`), it is a **single source**.
  Multi-source aggregation is impossible — all connections funnel into one
  throttled channel.
- If independent URLs exist (different CDNs/mirrors with separate bandwidth),
  it is a **true multi-source** file.

Load `references/strategy-and-pitfalls.md` for the full decision tree and the
measured pitfall table.

### Step 2 — Pick the strategy

**Single source → single-source multi-connection (preferred, this skill's scripts).**
Per-connection throttling (~15 KB/s measured) aggregates linearly with parallel
connections. Use `scripts/dl_parallel.py`:

```
# Direct signed URL already in hand:
python scripts/dl_parallel.py --out FILE.zip --total <bytes> --url-file .dl_url.txt

# Only a release download page URL known (auto-resolve the signed URL):
python scripts/dl_parallel.py --out FILE.zip --total <bytes> \
    --release-url https://github.com/OWNER/REPO/releases/download/TAG/ASSET

# Pre-warm just the signed URL into the url-file:
python scripts/dl_parallel.py --out FILE.zip --total <bytes> \
    --release-url <page-url> --resolve-only
```

Optional background token refresh so a mid-download expiry never interrupts
(the downloader re-reads the url-file before every 1MB packet):

```
python scripts/refresh_token.py --release-url <page-url> \
    --url-file .dl_url.txt --interval 540 &
```

**True multi-source → aria2.** Use independent URLs with aria2:

```
aria2c -x 16 -s 16 -k 1M -Z "url1" "url2" "url3"
```

### Step 3 — Verify

`dl_parallel.py` concatenates chunks, checks the final byte size against
`--total`, and runs `zipfile.testzip()` (skippable with `--no-zip-check`).
Look for `final=<n> expect=<n>` and `ZIP_OK` in the log (default `<out>.dl.log`).

## Scripts

- `scripts/dl_parallel.py` — single-source multi-connection downloader.
  Split a file into N threads; each thread downloads its range in 1MB
  append-mode sub-packets (resume on `IncompleteRead`/reset without re-fetching
  bytes). Reads the live URL from `--url-file` each packet. CLI params:
  `--out`, `--total` (required), `--url-file`, `--release-url`, `--threads`
  (default 64), `--sub` (default 1MB), `--work-dir`, `--log`, `--no-zip-check`,
  `--resolve-only`.
- `scripts/refresh_token.py` — daemon that resolves the signed URL from a
  release page URL (follows redirects, `geturl()`) and rewrites `--url-file`
  only on success. Params: `--release-url`, `--url-file`, `--interval`
  (default 540), `--once`.

## Critical pitfalls (measured on this machine — Windows + Git Bash/Cygwin)

1. **Cygwin fork limit ~30 background subprocesses** → `cygheap read copy
   failed, Win32 error 299` when spawning ~48 bash subshells. Use Python
   `threading`, not dozens of `curl &` processes.
2. **aria2 `--max-connection-per-server` is capped at 1–16**; `-x 32` is
   rejected. (16 connections alone gave only ~200 KB/s, so prefer the Python
   downloader for single sources.)
3. **aria2 Windows build does NOT support `--range`** — both `--range=X-Y` and
   `--range X-Y` fail as "unknown option". Abandon multi-process aria2 range
   splitting.
4. **ghproxy.net / ghproxy.com are unreliable relays** — they 302 to the same
   Azure blob and drop mid-transfer (`end of response with N bytes missing`),
   corrupting the file. Use them only to *resolve* the URL, never to transfer
   the payload.
5. **Per-connection throttling ~15 KB/s** — verified linear aggregation;
   parallel chunks are the fix.
6. **Signed-URL token expiry** — the JWT `exp` can expire mid-download; the
   edge cache HIT may still serve it, but run `refresh_token.py` to be safe.

See `references/strategy-and-pitfalls.md` for the full table, the speed-test
procedure, and the recommended constants (N=64, SUB=1MB, interval=540s).

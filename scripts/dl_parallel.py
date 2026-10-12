#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dl_parallel.py —— 单源多连接分片下载器（抗限速 / 抗抖动 / 断点续传）

适用场景：
  目标文件只有「一个真实来源」（例如 GitHub release-asset 签名直链
  release-assets.githubusercontent.com/...），但服务器对「单条连接」
  限速（实测约 15 KB/s），导致单线程下载极慢。通过 N 条并发连接聚合
  带宽，逼近单源速度上限。

核心设计（均已在 543MB GitHub 文件上实测验证）：
  1. 文件切成 N 段，每段一个线程并发下载（threading，避开 Git Bash
     Cygwin 的 fork 子进程数量上限 ~30）。
  2. 每段内部再以 1MB「小包」追加写入（append-mode）。任一包因
     IncompleteRead / 连接重置失败，只重传该 1MB 小包，不浪费已下载字节。
  3. 每个小包下载前「实时」读取 url-file 中的直链 —— 因此后台 token
     刷新守护脚本（refresh_token.py）随时覆盖 url-file，下载器无缝接管
     新签名 URL，不被过期打断。
  4. 拼合后校验总大小；可选 zipfile.testzip() 验证压缩包完整性。

用法：
  python dl_parallel.py --out FILE.zip --total 569649598 --url-file .dl_url.txt
  python dl_parallel.py --out FILE.zip --total 569649598 --release-url https://github.com/OWNER/REPO/releases/download/TAG/ASSET
  python dl_parallel.py --out FILE.zip --total 569649598 --release-url <url> --resolve-only

参数：
  --out         输出文件路径（必填）
  --total       文件总字节数（必填，可通过 HEAD 或发布页获取）
  --url-file    存放「直链」的文件；为空/缺失时尝试用 --release-url 解析（默认 <out>.url.txt）
  --release-url 规范的 release 下载页 URL；用于自动解析签名直链（可选）
  --threads     并发段数（默认 64，按机器与单连接限速调；64 在 8C 上稳定）
  --sub         小包字节数（默认 1000000 = 1MB）
  --work-dir    分片临时目录（默认 <out>.parts）
  --log         日志文件（默认 <out>.dl.log）
  --no-zip-check 跳过 zip 完整性校验
  --resolve-only 仅解析并写入 url-file 后退出（用于预热直链）
"""
import argparse
import os
import sys
import time
import threading
import urllib.request
import zipfile

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def resolve_signed_url(release_url, timeout=30):
    """从 release 下载页 URL 经重定向解析出最终的签名直链。

    返回最终 URL 字符串；失败返回空串。不下载响应体（仅取 geturl()）。
    """
    try:
        req = urllib.request.Request(release_url, headers={"User-Agent": UA})
        resp = urllib.request.urlopen(req, timeout=timeout)
        final = resp.geturl()
        resp.close()
        return final
    except Exception:
        return ""


def read_url(url_file):
    try:
        with open(url_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def write_url(url_file, url):
    try:
        with open(url_file, "w", encoding="utf-8") as f:
            f.write(url + "\n")
        return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--total", type=int, required=True)
    ap.add_argument("--url-file", default=None)
    ap.add_argument("--release-url", default=None)
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--sub", type=int, default=1_000_000)
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--log", default=None)
    ap.add_argument("--no-zip-check", action="store_true")
    ap.add_argument("--resolve-only", action="store_true")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    url_file = os.path.abspath(args.url_file) if args.url_file else (out + ".url.txt")
    work_dir = os.path.abspath(args.work_dir) if args.work_dir else (out + ".parts")
    log_file = os.path.abspath(args.log) if args.log else (out + ".dl.log")
    total = args.total
    n = max(1, args.threads)
    sub = max(1024, args.sub)
    chunk = (total + n - 1) // n

    os.makedirs(work_dir, exist_ok=True)

    loglock = threading.Lock()

    def log(msg):
        line = time.strftime("%T") + " " + msg
        with loglock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    # ---- 解析 / 预热直链 ----
    url = read_url(url_file)
    if not url and args.release_url:
        log("resolve from release-url ...")
        url = resolve_signed_url(args.release_url)
        if url:
            write_url(url_file, url)
            log("resolved: " + url[:120])
        else:
            log("resolve FAILED")
    if args.resolve_only:
        log("resolve-only done")
        return

    if not url:
        log("NO URL (set --url-file or --release-url)")
        sys.exit(4)

    # ---- 清理旧分片（全新开始；如需断点续传，删除此段）----
    for i in range(n):
        try:
            os.remove(os.path.join(work_dir, f"part_{i:03d}.bin"))
        except OSError:
            pass

    def download(idx, start, end):
        part = os.path.join(work_dir, f"part_{idx:03d}.bin")
        expect = end - start + 1
        for attempt in range(80):
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have >= expect:
                return True
            try:
                while have < expect:
                    sub_start = start + have
                    sub_end = min(sub_start + sub - 1, end)
                    u = read_url(url_file)
                    if not u:
                        time.sleep(2)
                        continue
                    req = urllib.request.Request(
                        u, headers={"Range": f"bytes={sub_start}-{sub_end}", "User-Agent": UA}
                    )
                    resp = urllib.request.urlopen(req, timeout=60)
                    data = resp.read()
                    resp.close()
                    if not data:
                        break
                    with open(part, "ab") as fp:
                        fp.write(data)
                    have += len(data)
                if have >= expect:
                    if os.path.getsize(part) != expect:
                        os.truncate(part, expect)
                    log(f"OK {idx} {expect}")
                    return True
            except Exception as e:
                if attempt % 10 == 0:
                    log(f"ERR {idx} a{attempt} {type(e).__name__}")
            time.sleep(1)
        log(f"FAIL {idx}")
        return False

    log(f"START n={n} chunk={chunk} total={total}")
    threads = []
    for i in range(n):
        s = i * chunk
        e = min(s + chunk - 1, total - 1)
        t = threading.Thread(target=download, args=(i, s, e), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    # ---- 校验分片 ----
    ok = True
    for i in range(n):
        p = os.path.join(work_dir, f"part_{i:03d}.bin")
        exp = min((i + 1) * chunk - 1, total - 1) - i * chunk + 1
        if not os.path.exists(p) or os.path.getsize(p) != exp:
            ok = False
            log(f"BAD part {i} size={os.path.getsize(p) if os.path.exists(p) else -1} exp={exp}")
    if not ok:
        log("ERROR parts incomplete")
        sys.exit(2)

    # ---- 拼合 ----
    log("concat")
    with open(out, "wb") as fo:
        for i in range(n):
            with open(os.path.join(work_dir, f"part_{i:03d}.bin"), "rb") as f:
                fo.write(f.read())
    final = os.path.getsize(out)
    log(f"final={final} expect={total}")
    if final != total:
        log("SIZE_MISMATCH")
        sys.exit(3)

    if not args.no_zip_check and out.lower().endswith(".zip"):
        try:
            z = zipfile.ZipFile(out)
            bad = z.testzip()
            log("ZIP_OK" if bad is None else f"ZIP_BAD {bad}")
        except Exception as e:
            log(f"ZIP_CHECK_ERR {e}")
    log("DONE")


if __name__ == "__main__":
    main()

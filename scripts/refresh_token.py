#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_token.py —— 签名直链刷新守护进程

背景：
  GitHub release-asset 直链是带 SAS 令牌的 Azure blob URL，令牌含 JWT 的
  exp 过期时间。虽实测边缘缓存 HIT 下「过期后仍可下载」，但为稳妥，后台
  持续刷新直链。下载器（dl_parallel.py）每下载 1MB 小包前都会「实时」读取
  url-file，因此本脚本覆盖 url-file 后无需重启下载即可无缝切换新令牌。

原理：
  对规范的 release 下载页 URL 发 GET（跟随重定向），用 urlopen.geturl()
  取得最终签名直链，仅当解析成功且与当前内容不同才写回 url-file。

用法：
  python refresh_token.py --release-url <url> --url-file .dl_url.txt --interval 540
  python refresh_token.py --release-url <url> --url-file .dl_url.txt --once

参数：
  --release-url  规范的 release 下载页 URL（必填）
  --url-file     直链写出文件（必填）
  --interval     刷新间隔秒（默认 540；Github 直链常 ~1h 有效，留冗余）
  --once         只刷新一次即退出（用于预热）
"""
import argparse
import os
import sys
import time
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def resolve_signed_url(release_url, timeout=30):
    try:
        req = urllib.request.Request(release_url, headers={"User-Agent": UA})
        resp = urllib.request.urlopen(req, timeout=timeout)
        final = resp.geturl()
        resp.close()
        return final
    except Exception as e:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release-url", required=True)
    ap.add_argument("--url-file", required=True)
    ap.add_argument("--interval", type=int, default=540)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    url_file = os.path.abspath(args.url_file)

    def current():
        try:
            with open(url_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def refresh():
        new = resolve_signed_url(args.release_url)
        if not new:
            print(time.strftime("%T") + " resolve FAILED, keep old", flush=True)
            return False
        if new != current():
            try:
                with open(url_file, "w", encoding="utf-8") as f:
                    f.write(new + "\n")
                print(time.strftime("%T") + " refreshed url-file", flush=True)
            except OSError as e:
                print(time.strftime("%T") + f" write err {e}", flush=True)
                return False
        else:
            print(time.strftime("%T") + " url unchanged", flush=True)
        return True

    refresh()
    if args.once:
        return
    while True:
        time.sleep(args.interval)
        refresh()


if __name__ == "__main__":
    main()

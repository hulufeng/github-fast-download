# GitHub 大文件加速下载：策略决策树与实测陷阱

本参考记录「慢速 GitHub 下载」的诊断与落地方法，以及在本机（Windows +
Git Bash / Cygwin + 国内网络）实测踩过的坑。所有结论均来自一次 543MB
Ghidra release 文件（569649598 字节）的真实下载对抗。

---

## 1. 先判断「到底能不能搞多源」

用户常说「像迅雷一样多线程 / 多源加速」。但「多源」是否成立，取决于文件
有几个**真实**来源：

- **单源文件**：所有镜像 / 代理最终都 302 回同一个 blob。
  例：`release-assets.githubusercontent.com/...` 是 Azure blob 签名直链，
  ghproxy.net / ghproxy.com 等中继都只是 302 跳回它。**多源聚合在这里
  不可能** —— 你开的每个连接最终都挤在同一条限速通道上。
  → 解法：**单源多连接**（本 Skill 的 `dl_parallel.py`，64 线程）。

- **真多源文件**：存在多个独立 URL（不同 CDN / 镜像 / 官方多节点），
  各自有独立带宽。
  → 解法：**aria2 多 URL**（`aria2c -Z url1 url2 ...`，每服务器上限 16 连接）。

**判定方法**：用 `curl -I` 顺着下载页 URL 看 `location:` 跳转；若所有
候选镜像最终 302 到同一个 host（如 `release-assets.githubusercontent.com`），
即为单源。

---

## 2. 单源：先确认是「按连接限速」

单条 TCP 连接被限速时，开 N 条连接带宽线性叠加。验证：

```
# 单连接测速
curl -r 0-10000000 -o /dev/null -w "%{speed_download}\n" <直链>
# 8 连接并发测速（各自 range 不同段）
```

实测：单连接 ≈ 15 KB/s；8 连接 ≈ 116 KB/s（近似线性）。说明是
**per-connection 限速**，并行分片有效。本机单源速度上限约 600 KB/s ~ 1.1 MB/s。

---

## 3. 实现选型（单源多连接）

### 3.1 为什么不用 bash 多进程
Git Bash / Cygwin 对后台子进程数量有硬限制（约 30 个）。
实测开 48 个 `curl &` 后台子shell 时直接报错：
`cygheap read copy failed, Win32 error 299`（fork 失败）。
→ 改用 **Python `threading`**（线程不触发 fork 限制），稳定 64 线程。

### 3.2 为什么用「1MB 小包 + 追加续写」而非整段下载
整段（如 8.9MB）下载时一旦触发 `IncompleteRead` / 连接重置，整段作废、
从头重来，浪费已下字节。改为每段内部再以 1MB 小包 `Range` 追加写
（`open(part,'ab')` + `os.path.getsize` 计算偏移），单包失败只重传 1MB。

### 3.3 抗 token 过期：实时读 url-file
签名直链含 JWT `exp`，会过期。让下载器**每个小包下载前都重新读一次
url-file 里的直链**，后台 `refresh_token.py` 随时覆盖该文件即可无缝切换，
无需重启下载。

---

## 4. 实现选型（真多源）

用 aria2（GitHub 30k+ stars，开源多连接 / 多源下载器）：

```
aria2c -x 16 -s 16 -k 1M -Z "url1" "url2" "url3"
```

- `-x` / `--max-connection-per-server`：**仅支持 1–16**，超出报错。
- `-s` / `--split`：分段数。
- `-Z` / `--force-sequential` + 多 URL = 多源。

---

## 5. 实测陷阱清单（必读）

| 陷阱 | 现象 | 处理 |
|------|------|------|
| Cygwin fork 上限 ~30 | `cygheap read copy failed, Win32 error 299` | 用 Python threading，别开几十个 bash 子进程 |
| aria2 连接数上限 | `-x 32` 被拒 | 最高 `-x 16`（实测 16 连接仅 ~200KB/s，故单源优先 Python） |
| aria2 Win 版不支持 `--range` | `--range=X-Y` 与 `--range X-Y` 均报 `unknown option` | 放弃「多进程各下一段」的 aria2 方案 |
| ghproxy 中继掉速 / 中断 | `end of response with 450147 bytes missing`，zip 残缺 | 仅用 ghproxy 解析直链（HEAD location），**不要**经它传输大文件 |
| 单连接限速 ~15KB/s | 单线程 ETA 10h | 并行分片（64 线程） |
| 直链 token 过期 | 下载中途 403 | 后台 refresh_token 守护 + 下载器实时读数 |
| 边缘缓存 HIT | 链接显示过期但仍 200 | 可临时续下；仍建议刷新以防万一 |

---

## 6. 落地步骤（单源典型流程）

1. 取得直链：
   - 用户给的是 `release-assets.githubusercontent.com/...` 直链 → 直接写入 url-file。
   - 用户给的是 `github.com/.../releases/download/...` 页 → 用
     `refresh_token.py --once` 或 `dl_parallel.py --resolve-only` 解析。
2. 启动刷新守护（可选但推荐）：
   `python refresh_token.py --release-url <页URL> --url-file .dl_url.txt --interval 540 &`
3. 启动下载：
   `python dl_parallel.py --out FILE.zip --total <字节数> --url-file .dl_url.txt`
4. 校验：`final` 大小一致 + `ZIP_OK`（脚本自动做；非 zip 用 `--no-zip-check`）。

> 总字节数获取：对直链 `curl -I` 看 `Content-Length`；或 release 页 API。

---

## 7. 关键常量（本次实测基准，按需调整）

- 并发段数 `N=64`：在 8 核机器上稳定；可按 CPU 核数与单连接限速增减。
- 小包 `SUB=1_000_000`（1MB）：平衡续传粒度与请求开销。
- 刷新间隔 `540s`：Github 直链通常 ~1h 有效，留冗余。
- 单包重试 `80` 次，失败间隔 1s。

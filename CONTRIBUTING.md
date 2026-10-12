# 贡献指南（CONTRIBUTING）

感谢你对 **github-fast-download** 的兴趣！欢迎通过 Issue 与 Pull Request 改进这个 WorkBuddy 技能。

## 如何贡献

- **提 Bug / 建议**：在 Issues 中尽量说清复现步骤、运行环境（系统版本、是否 Git Bash / Cygwin）、下载源类型（单源还是真多源）以及实测带宽。
- **改脚本**：`scripts/` 下的 Python 脚本请保持**纯标准库实现**（不依赖第三方包），并保留两项核心设计：
  - 1MB 小包追加续写（抗 `IncompleteRead` / 连接重置，不重传已下字节）；
  - 每个小包下载前实时读取 `url-file`（抗签名直链 token 过期）。
- **改文档**：主文档是 `SKILL.md` 与 `references/strategy-and-pitfalls.md`，若逻辑变化请同步更新。

## 本地预览

本仓库本身即可作为技能目录使用：把它软链或复制到 `~/.workbuddy/skills/github-fast-download`，重启 / 刷新 WorkBuddy 后即可在慢速下载场景下触发验证。

## 许可证

本仓库采用 **CC BY-NC 4.0** 许可（署名—非商业性使用 4.0 国际）。提交贡献即表示你同意你的贡献在相同许可下发布。详见 [LICENSE](LICENSE)。

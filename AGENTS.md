# AGENTS.md

StackLens：把微信小游戏底包的压缩堆栈还原成 TypeScript 源码级堆栈。使用者通常是
打底包 / 看崩溃日志的同学，改动要保守，宁可少做也不要破坏已有解析结果。

## 文件分工

- `app.py`：Tkinter 界面 + 命令行入口 + `--selftest` 自检
- `core.py`：全部可测试的逻辑（sourcemap 解析、帧分类、报告渲染），不依赖界面
- `build_macos.sh`、`build_windows.bat`：PyInstaller 打包
- `.github/workflows/build.yml`：push 到 main 自动构建 mac / windows 产物

## 核心数据结构

- `SourceMap`：一个 `*.js.map`。`mappings` 延迟解码，`sourcesContent` 用来取源码行。
- `BundleIndex`：一个底包目录下所有 map 的集合，负责「JS 帧 → 源码帧」和
  「TS 帧 → 源码行」两种反查。
- `ResolvedFrame`：解析后的单帧，带 `category`（业务/框架/平台/转译）和上下文源码。

## 硬性约束

- 只依赖 Python 标准库。加第三方依赖前先确认它对 PyInstaller 双平台打包友好。
- **绝不提交底包、堆栈原文、真实业务日志**。`.gitignore` 已挡常见产物，按需补充。
- 不提交构建产物：`*.app/`、`build/`、`dist/`、`.venv/` 都已忽略。
- PyInstaller 不能跨系统打包：mac 上只能出 `.app`，`.exe` 必须靠 Windows 机器或 CI。
- macOS 产物是 arm64（Apple 芯片）；Intel Mac 需要自行打包。

## 改完必须验证

```bash
python app.py --selftest
```

**改动 `core.py` 的解析逻辑时**，必须带上真实底包做端到端验证：

```bash
python app.py --selftest /path/to/底包目录 /path/to/stack.txt
```

这一项会逐帧断言「业务帧都能取到源码行」，是防止解析退化的主要防线。

改界面时至少确认窗口能起来，且 `winfo_reqwidth/reqheight` 不超过设定的 geometry，
否则内容会被裁掉。

## 已知的坑

- 底包里 `sources` 的路径会被拼坏成
  `file:/Users/jenkins/.../debugToolLogic/file:/Users/jenkins/.../DebugToolBase.ts`，
  统一走 `clean_source_path()` 处理，别在别处再写一套。
- `subpackages/*/index.js.map` 的 `file` 字段全是 `all.js`，按文件名消歧没用，
  必须靠目录 / 相邻帧判断。
- 裸文件名（`index.ts`）在底包里大量撞名，只有「全局唯一」才允许直接采用；
  否则走相邻帧参照，并且要反复扫直到没有新进展（相邻帧本身可能也是裸文件名）。
- 微信开发者工具给出的堆栈可能已经是 `.ts` 也可能是 `.js`，两条路径都要保证正确。

## 发布

push 到 main 后 CI 会自动出两个 artifact，但 artifact 需要登录且 90 天过期。
对外发用 Release（mac 的 zip 用 `ditto -c -k --keepParent stacklens.app out.zip`
打，这样才保得住 app 的可执行权限）：

```bash
gh release create vX.Y.Z --repo yehuzai/stacklens --notes-file NOTES.md \
  stacklens-macos.zip stacklens.exe
```

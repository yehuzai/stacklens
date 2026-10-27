# StackLens

把微信小游戏 / Cocos Creator 底包里的压缩堆栈，还原成 TypeScript 源码级堆栈：
每一帧都能看到原始 `.ts` 文件、行号，以及那一行代码。

## 它解决什么问题

底包里跑出来的报错长这样，函数名是压缩后的 `Ht`、`Gt`，你根本不知道对应哪行代码：

```
r.registerToolClass    @    DebugToolBase.ts:169
Ht    @    index.ts:67
a.InitEntry    @    MassCustomTool.ts:30
Gt    @    index.ts:50
i.onInit    @    index.ts:22
```

StackLens 读底包里的 `*.js.map`，把这些帧还原成：

```
#3  debugToolLogic/DebugToolBase.ts:169   r.registerToolClass
#4  debugToolLogic/index.ts:67            Ht
#5  debugToolComps/MassCustomTool.ts:30   a.InitEntry
#14 debugToolLogic/index.ts:50            Gt
#16 debugToolLogic/index.ts:22            i.onInit
```

并直接把命中那一行的源码打出来，前后带上下文。

## 直接使用

**macOS**：双击 `stacklens.app`。首次打开若被 Gatekeeper 拦，右键点 app →「打开」，或

```bash
xattr -dr com.apple.quarantine /path/to/stacklens.app
```

**Windows**：双击 `stacklens.exe`（单文件，免安装）。被 SmartScreen 拦时点
「更多信息」→「仍要运行」。

界面上填三样东西就能用：

1. **底包目录** —— 解压后的底包根目录（里面能看到 `game.js`、`subpackages/`）
2. **堆栈** —— 从微信开发者工具整段粘贴
3. **仓库 / tag（可选）** —— 填了会额外显示每个帧对应的 git 提交人和提交说明

## 命令行

```bash
# 解析一段堆栈
python app.py resolve ./底包目录 stack.txt

# 从标准输入
pbpaste | python app.py resolve ./底包目录 -

# 只看业务帧，不带上下文（适合贴到工单里）
python app.py resolve ./底包目录 stack.txt --no-context

# 展开平台 / 框架 / 转译层噪音
python app.py resolve ./底包目录 stack.txt --all

# 输出 JSON，便于接别的工具
python app.py resolve ./底包目录 stack.txt --json

# 配上 git 仓库和 tag，显示 blame
python app.py resolve ./底包目录 stack.txt --repo ~/Documents/PandoraCreator --tag gly_launch_opt.261027.01

# 看看底包里有哪些 sourcemap
python app.py scan ./底包目录
```

## 原理

1. 扫描底包目录下所有 `*.js.map`。
2. 微信开发者工具给出的堆栈可能已经是 `.ts` 文件名（它自己映射过一层），
   也可能还是 `.js`。两种情况都处理：
   - `.ts` 帧：直接在 sourcemap 的 `sources` 里按路径后缀匹配，取 `sourcesContent` 的对应行。
   - `.js` 帧：用 VLQ 解 `mappings`，按生成行列反查原始位置。
3. 裸文件名（`index.ts`、`node.ts`）会撞名 —— 底包里可能有几十个同名文件。
   这时用相邻帧已经确定的 sourcemap 作参照来消歧，而不是靠猜路径长度。
4. 按路径特征把帧分成**业务 / 框架 / 平台 / 转译**，默认只展开业务帧，
   把 `_ts_generator`、`rollupPluginModLoBabelHelpers`、Cocos 事件系统、
   `WAGame.js` 这些噪音折叠起来。

## 环境要求与从源码运行

需要 Python 3.9+ 且 `tkinter` 可用。

```bash
pip install -r requirements.txt
python app.py                    # 图形界面
python app.py --selftest         # 无界面自检
```

打包：mac 上 `bash build_macos.sh`；Windows 上运行 `build_windows.bat`。
PyInstaller 不能跨系统打包，`.exe` 必须在 Windows 机器或 CI 上生成。

带真实底包的自检（会校验每帧都能取到源码行）：

```bash
python app.py --selftest ./底包目录 stack.txt
```

## 已知边界

- 底包里没有 `sourcesContent` 的 sourcemap，只能给出位置、拿不到源码行。
- 没做苹果签名/公证，macOS 首次打开需要手动放行。
- macOS 产物是 arm64（Apple 芯片）；Intel Mac 需要自行打包。

## 说明

- 开发约定见 [AGENTS.md](AGENTS.md)。
- 发布说明见 [NOTES.md](NOTES.md)。

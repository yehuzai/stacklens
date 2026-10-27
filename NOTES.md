把 StackLens 打包成 macOS 和 Windows 都能直接双击运行的版本，目标机器不用装任何东西。

## 下载哪个

| 系统 | 文件 | 用法 |
| --- | --- | --- |
| macOS（Apple 芯片 / M 系列） | `stacklens-macos.zip` | 解压后得到 `stacklens.app`，双击运行 |
| Windows 64 位 | `stacklens.exe` | 直接双击，免安装 |

## 怎么用

界面上填三样：

1. **底包目录**：解压后的底包根目录，能看见 `game.js`、`subpackages/` 的那一层。
2. **堆栈**：从微信开发者工具整段粘贴（或点「从剪贴板粘贴」）。
3. **仓库 / tag**：可选。填了会在每帧后面附上 git 提交人和提交说明，
   方便判断这行代码是哪个版本改的。

点「解析堆栈」，结果区会按「业务 / 框架 / 平台 / 转译」分类，
默认只展开业务帧，其余折叠成一行摘要。

## 首次打开被系统拦截怎么办

- macOS：未做苹果签名/公证。提示「来自身份不明的开发者」时，右键点 app →「打开」，
  或先在终端跑 `xattr -dr com.apple.quarantine stacklens.app`。
- Windows：SmartScreen 拦截时点「更多信息」→「仍要运行」。

## 校验下载是否完整（SHA-256）

```
<macos zip 的 sha256>  stacklens-macos.zip
<exe 的 sha256>  stacklens.exe
```

#!/usr/bin/env python3
"""StackLens —— 把微信小游戏底包里的压缩堆栈还原成 TypeScript 源码级堆栈。

两种用法：
  图形界面：双击运行，或  python app.py
  命令行：  python app.py resolve <底包目录> <堆栈文件|->  [选项]
  自检：    python app.py --selftest

命令行选项：
  --no-context     只输出命中那一行，不带上下文
  --all            非业务帧也展开
  --json           输出 JSON
  --repo PATH      额外对上 git 仓库（可选）
  --tag TAG        配合 --repo，定位到指定 tag
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import traceback

import core

APP_TITLE = "StackLens"
APP_VERSION = "1.0.0"


def configure_console_encoding():
    """Keep Chinese CLI output usable on Windows CI and legacy terminals."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


if sys.platform == "darwin":
    BASE_FONT = ("PingFang SC", 13)
    MONO = ("Menlo", 12)
elif os.name == "nt":
    BASE_FONT = ("Microsoft YaHei UI", 10)
    MONO = ("Consolas", 10)
else:
    BASE_FONT = ("Sans", 10)
    MONO = ("Monospace", 10)


# ---------------------------------------------------------------------------
# 任务执行
# ---------------------------------------------------------------------------

def run_job(bundle_dir, stack_text, log, repo_path="", tag="",
            show_all=False, show_context=True):
    """核心流程：载入底包 -> 逐帧解析 -> 返回可读报告。"""
    if not bundle_dir:
        raise ValueError("还没选底包目录")
    if not (stack_text or "").strip():
        raise ValueError("堆栈内容是空的")

    log("扫描底包：%s" % bundle_dir)
    index = core.BundleIndex(bundle_dir, progress=log)
    stats = index.stats()
    log("找到 %d 个 sourcemap、%d 个源文件" % (stats["maps"], stats["sources"]))

    headline, frames = core.resolve_stack(index, stack_text, progress=log)

    repo = None
    if repo_path:
        log("对上 git 仓库：%s%s" % (repo_path, (" @ " + tag) if tag else ""))
        repo = core.GitRepo(repo_path, tag)
        if repo._ok:
            core.enrich_with_git(frames, repo)
        else:
            log("  " + (repo.error or "git 仓库不可用"))

    report = core.format_report(
        headline, frames, show_all=show_all, show_context=show_context, info=stats)
    return report, headline, frames, stats


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def cmd_resolve(args):
    log = lambda m: print("[stacklens] " + m, file=sys.stderr)  # noqa: E731
    if args.stack == "-":
        stack_text = sys.stdin.read()
    else:
        with open(args.stack, "r", encoding="utf-8", errors="replace") as fh:
            stack_text = fh.read()

    report, headline, frames, stats = run_job(
        args.bundle, stack_text, log, repo_path=args.repo or "", tag=args.tag or "",
        show_all=args.all, show_context=not args.no_context)

    if args.json:
        print(json.dumps(core.report_json(headline, frames), ensure_ascii=False, indent=2))
    else:
        print(report)
    return 0


def cmd_scan(args):
    index = core.BundleIndex(args.bundle)
    stats = index.stats()
    print("底包：%s" % stats["root"])
    print("sourcemap：%d 个，源文件 %d 个" % (stats["maps"], stats["sources"]))
    print()
    for m in index.maps:
        print("  %-42s 源文件 %5d  %s" % (
            m.bundle_rel, len(m.sources),
            "含源码" if m.has_content else "无 sourcesContent"))
    return 0


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------

def build_app():
    import tkinter as tk
    from tkinter import filedialog, ttk

    root = tk.Tk()
    root.title("%s  %s" % (APP_TITLE, APP_VERSION))
    root.minsize(720, 640)

    style = ttk.Style(root)
    style.configure(".", font=BASE_FONT)
    style.configure("Title.TLabel", font=(BASE_FONT[0], BASE_FONT[1] + 5, "bold"))
    style.configure("Hint.TLabel", foreground="#667085")

    app = {"busy": False, "q": queue.Queue(), "result": ""}

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, text="把底包目录和报错堆栈贴进来，还原成 TypeScript 源码位置",
              style="Hint.TLabel").pack(anchor="w", pady=(0, 8))

    # 底包目录
    row = ttk.Frame(outer)
    row.pack(fill="x", pady=(0, 6))
    ttk.Label(row, text="底包目录", width=9).pack(side="left")
    bundle_var = tk.StringVar()
    ttk.Entry(row, textvariable=bundle_var).pack(side="left", fill="x", expand=True, padx=6)

    def pick_bundle():
        d = filedialog.askdirectory(title="选择解压后的底包目录")
        if d:
            bundle_var.set(d)
    ttk.Button(row, text="选择…", command=pick_bundle).pack(side="left")

    # git 仓库（可选）
    row2 = ttk.Frame(outer)
    row2.pack(fill="x", pady=(0, 6))
    ttk.Label(row2, text="仓库(可选)", width=9).pack(side="left")
    repo_var = tk.StringVar()
    ttk.Entry(row2, textvariable=repo_var).pack(side="left", fill="x", expand=True, padx=6)

    def pick_repo():
        d = filedialog.askdirectory(title="选择 git 仓库目录（可选）")
        if d:
            repo_var.set(d)
    ttk.Button(row2, text="选择…", command=pick_repo).pack(side="left")

    row3 = ttk.Frame(outer)
    row3.pack(fill="x", pady=(0, 8))
    ttk.Label(row3, text="tag(可选)", width=9).pack(side="left")
    tag_var = tk.StringVar()
    ttk.Entry(row3, textvariable=tag_var, width=28).pack(side="left", padx=6)
    show_all_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(row3, text="展开非业务帧", variable=show_all_var).pack(side="left", padx=6)
    ctx_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(row3, text="显示上下文", variable=ctx_var).pack(side="left")

    panes = ttk.Panedwindow(outer, orient="vertical")
    panes.pack(fill="both", expand=True)

    left = ttk.Frame(panes)
    ttk.Label(left, text="堆栈（从调试工具里整段粘贴）", style="Hint.TLabel").pack(anchor="w")
    inp = tk.Text(left, height=9, font=MONO, relief="solid", borderwidth=1, undo=True)
    inp.pack(fill="both", expand=True, pady=(2, 0))
    panes.add(left, weight=1)

    right = ttk.Frame(panes)
    ttk.Label(right, text="解析结果", style="Hint.TLabel").pack(anchor="w")
    out = tk.Text(right, height=16, font=MONO, relief="solid", borderwidth=1,
                  background="#ffffff", wrap="none")
    ysb = ttk.Scrollbar(right, orient="vertical", command=out.yview)
    xsb = ttk.Scrollbar(right, orient="horizontal", command=out.xview)
    out.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
    out.pack(side="left", fill="both", expand=True, pady=(2, 0))
    ysb.pack(side="right", fill="y", pady=(2, 0))
    panes.add(right, weight=3)

    act = ttk.Frame(outer)
    act.pack(fill="x", pady=(8, 0))
    run_btn = ttk.Button(act, text="解析堆栈")
    run_btn.pack(side="left")
    status = ttk.Label(act, text="就绪", style="Hint.TLabel")
    status.pack(side="left", padx=10)

    def copy_result():
        if app["result"]:
            root.clipboard_clear()
            root.clipboard_append(app["result"])
            status.configure(text="已复制到剪贴板")

    def paste_stack():
        try:
            inp.delete("1.0", "end")
            inp.insert("1.0", root.clipboard_get())
            status.configure(text="已从剪贴板粘贴")
        except tk.TclError:
            status.configure(text="剪贴板是空的")

    ttk.Button(act, text="从剪贴板粘贴", command=paste_stack).pack(side="left", padx=6)
    ttk.Button(act, text="复制结果", command=copy_result).pack(side="left")

    def log(line):
        app["q"].put(("log", line))

    def drain():
        try:
            while True:
                kind, payload = app["q"].get_nowait()
                if kind == "log":
                    status.configure(text=payload)
                elif kind == "result":
                    out.configure(state="normal")
                    out.delete("1.0", "end")
                    out.insert("1.0", payload)
                    out.configure(state="disabled")
                    app["result"] = payload
                elif kind == "done":
                    run_btn.configure(state="normal")
                    app["busy"] = False
                    status.configure(text=payload)
        except queue.Empty:
            pass
        root.after(80, drain)

    def start():
        if app["busy"]:
            return
        app["busy"] = True
        run_btn.configure(state="disabled")
        status.configure(text="解析中…")
        bundle = bundle_var.get().strip()
        repo = repo_var.get().strip()
        tag = tag_var.get().strip()
        text = inp.get("1.0", "end")
        allf = show_all_var.get()
        ctx = ctx_var.get()

        def worker():
            try:
                report, _h, _f, _s = run_job(
                    bundle, text, log, repo_path=repo, tag=tag,
                    show_all=allf, show_context=ctx)
                app["q"].put(("result", report))
                app["q"].put(("done", "完成"))
            except Exception as exc:  # noqa: BLE001
                app["q"].put(("result", "解析失败：%s" % exc))
                app["q"].put(("done", "失败"))
                traceback.print_exc()

        threading.Thread(target=worker, daemon=True).start()

    run_btn.configure(command=start)

    root.update_idletasks()
    w = min(max(760, root.winfo_reqwidth() + 40), root.winfo_screenwidth() - 60)
    h = min(max(660, root.winfo_reqheight() + 40), root.winfo_screenheight() - 120)
    root.geometry("%dx%d" % (w, h))

    drain()
    return root


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def selftest(argv):
    """无界面跑一遍完整流程，CI 和打包后都靠它验证。"""
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("  [ok]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail and not cond else ""))
        if not cond:
            ok = False

    print("1. VLQ 解码")
    check("空串", core.decode_vlq("") == [])
    check("AAgB 解码", core.decode_vlq("AAgB") == [0, 0, 16], str(core.decode_vlq("AAgB")))
    check("AAAA 解码", core.decode_vlq("AAAA") == [0, 0, 0, 0])
    # 负数：首位是符号位，'D' = 3 -> 值 1 取负 = -1
    check("负数解码", core.decode_vlq("D") == [-1], str(core.decode_vlq("D")))

    print("2. 路径清洗")
    raw = ("file:/Users/jenkins/x/file:/Users/jenkins/x/assets/zdev/DebugToolBase.ts")
    check("折叠重复 file: 前缀",
          core.clean_source_path(raw).endswith("assets/zdev/DebugToolBase.ts"),
          core.clean_source_path(raw))
    check("去查询串", core.clean_source_path("a/b.ts?x=1") == "a/b.ts")

    print("3. 堆栈切帧")
    sample = "\n".join([
        "xxx is not defined",
        "(env: macOS,mg,2.02.2608070; lib: 3.17.5)",
        "Ht    @    index.ts:67",
        "(anonymous)    @    rollupPluginModLoBabelHelpers.js:1675",
        "    at foo (bar.js:10:5)",
    ])
    head, frames = core.parse_stack(sample)
    check("识别错误标题", head == "xxx is not defined", head)
    check("帧数 3", len(frames) == 3, str(len(frames)))
    check("微信格式解析", frames[0].func == "Ht" and frames[0].line == 67)
    check("V8 格式解析", frames[2].func == "foo" and frames[2].column == 5)

    print("4. 对着真实底包解析")
    if len(argv) >= 1 and os.path.isdir(argv[0]):
        bundle = argv[0]
        stack_file = argv[1] if len(argv) > 1 else ""
        if stack_file and os.path.isfile(stack_file):
            with open(stack_file, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            text = "err\nr.registerToolClass    @    DebugToolBase.ts:169"
        index = core.BundleIndex(bundle)
        st = index.stats()
        check("扫到 sourcemap", st["maps"] > 0, str(st))
        headline, res = core.resolve_stack(index, text)
        biz = [f for f in res if f.category == "业务"]
        check("有业务帧", len(biz) > 0, str(len(biz)))
        for f in biz:
            check("业务帧能取到源码行：%s" % f.location, bool(f.text.strip()), f.note)
    else:
        print("  (跳过：没传底包目录)")

    print()
    print("自检通过" if ok else "自检失败")
    return 0 if ok else 1


# ---------------------------------------------------------------------------

def main():
    configure_console_encoding()
    argv = sys.argv[1:]
    if "--selftest" in argv:
        rest = [a for a in argv if a != "--selftest"]
        return selftest(rest)

    if argv and argv[0] in ("resolve", "scan"):
        parser = argparse.ArgumentParser(prog="stacklens", description="压缩堆栈 -> TS 源码级堆栈")
        sub = parser.add_subparsers(dest="cmd", required=True)

        p = sub.add_parser("resolve", help="解析一段堆栈")
        p.add_argument("bundle", help="解压后的底包目录")
        p.add_argument("stack", help="堆栈文本文件，- 表示从标准输入读")
        p.add_argument("--repo", help="git 仓库目录（可选）")
        p.add_argument("--tag", help="配合 --repo，定位到指定 tag")
        p.add_argument("--json", action="store_true", help="输出 JSON")
        p.add_argument("--all", action="store_true", help="展开非业务帧")
        p.add_argument("--no-context", action="store_true", help="只输出命中行")
        p.set_defaults(func=cmd_resolve)

        p2 = sub.add_parser("scan", help="列出底包里的 sourcemap")
        p2.add_argument("bundle", help="解压后的底包目录")
        p2.set_defaults(func=cmd_scan)

        args = parser.parse_args(argv)
        return args.func(args)

    try:
        build_app().mainloop()
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

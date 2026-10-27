#!/usr/bin/env python3
"""StackLens 核心逻辑。

职责：把微信小游戏底包（或任意带 sourcemap 的 JS 产物）里的压缩堆栈，
还原成 TypeScript 源码级的 文件:行:列 + 该行源码。

这里的代码全部不依赖界面，`app.py --selftest` 和 CI 都能无界面跑通。
"""

from __future__ import annotations

import bisect
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Base64 VLQ —— sourcemap v3 的 mappings 就是用这个编码的
# ---------------------------------------------------------------------------

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_B64_INDEX = {c: i for i, c in enumerate(_B64)}


def decode_vlq(segment: str) -> List[int]:
    """把一段 base64 VLQ 解成整数列表。"""
    values: List[int] = []
    shift = 0
    value = 0
    for ch in segment:
        digit = _B64_INDEX[ch]
        cont = digit & 32
        digit &= 31
        value += digit << shift
        if cont:
            shift += 5
        else:
            neg = value & 1
            value >>= 1
            values.append(-value if neg else value)
            value = 0
            shift = 0
    return values


def decode_mappings(mappings: str) -> List[List[Tuple[int, Optional[int], Optional[int], Optional[int], Optional[int]]]]:
    """解 mappings 串。

    返回按「生成行」分组的段列表，每段是
    (生成列, 源文件下标, 源行, 源列, 名字下标)，后三项对 1 字段段为 None。
    """
    result: List[List[Tuple[int, Optional[int], Optional[int], Optional[int], Optional[int]]]] = []
    src = 0
    src_line = 0
    src_col = 0
    name = 0

    for line_str in mappings.split(";"):
        gen_col = 0
        segments: List[Tuple[int, Optional[int], Optional[int], Optional[int], Optional[int]]] = []
        if line_str:
            for seg in line_str.split(","):
                if not seg:
                    continue
                vals = decode_vlq(seg)
                if not vals:
                    continue
                gen_col += vals[0]
                if len(vals) >= 4:
                    src += vals[1]
                    src_line += vals[2]
                    src_col += vals[3]
                    name_idx: Optional[int] = None
                    if len(vals) >= 5:
                        name += vals[4]
                        name_idx = name
                    segments.append((gen_col, src, src_line, src_col, name_idx))
                else:
                    segments.append((gen_col, None, None, None, None))
        result.append(segments)
    return result


# ---------------------------------------------------------------------------
# 源文件路径清洗
# ---------------------------------------------------------------------------

_FILE_PREFIX_RE = re.compile(r"^(?:webpack|rollup)?://+")
_QUERY_RE = re.compile(r"\?.*$")


def clean_source_path(raw: str) -> str:
    """把 sourcemap 里的 sources[i] 清洗成正常的绝对/相对路径。

    底包里会看到这种被拼坏的路径：
        file:/Users/jenkins/.../debugToolLogic/file:/Users/jenkins/.../DebugToolBase.ts
    处理办法是去掉所有 file: 前缀，只保留最后一截。
    """
    p = (raw or "").replace("\\", "/")
    p = _FILE_PREFIX_RE.sub("", p)
    if "file:" in p:
        p = p.split("file:")[-1]
    p = _QUERY_RE.sub("", p)
    # 折叠 ./ 和 a/../b
    if "/./" in p or p.startswith("./") or "/../" in p:
        parts: List[str] = []
        for part in p.split("/"):
            if part in ("", "."):
                continue
            if part == ".." and parts and parts[-1] != "..":
                parts.pop()
                continue
            parts.append(part)
        p = ("/" if p.startswith("/") else "") + "/".join(parts)
    return p


def short_path(path: str, keep: int = 3) -> str:
    """把长路径缩成末尾 keep 段，用于展示。"""
    parts = [x for x in path.split("/") if x]
    if len(parts) <= keep:
        return "/".join(parts)
    return "/".join(parts[-keep:])


# ---------------------------------------------------------------------------
# 单个 sourcemap
# ---------------------------------------------------------------------------

class SourceMap:
    """一个 *.js.map 的封装。mappings 延迟解码，避免一次性吃掉大底包的内存。"""

    def __init__(self, path: str, root: str = ""):
        self.path = path
        self.root = root
        self.bundle_rel = os.path.relpath(path, root) if root else path
        self._data: Optional[dict] = None
        self._decoded: Optional[List[List[Any]]] = None
        self._cols: List[List[int]] = []
        self._sources_clean: List[str] = []
        self._content_lines: List[List[str]] = []
        self._names: List[str] = []
        self.load_error: str = ""

    # -- 载入 -------------------------------------------------------------
    def _ensure(self) -> bool:
        if self._data is not None:
            return True
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                self._data = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            self.load_error = "读取 sourcemap 失败：%s" % exc
            self._data = {}
            return False
        self._sources_clean = [clean_source_path(s) for s in self._data.get("sources") or []]
        self._names = self._data.get("names") or []
        contents = self._data.get("sourcesContent") or []
        self._content_lines = [
            (c.split("\n") if isinstance(c, str) else []) for c in contents
        ]
        return True

    @property
    def js_file(self) -> str:
        """这个 map 对应的 JS 文件名（map 里声明的 file 字段）。"""
        self._ensure()
        return (self._data or {}).get("file") or os.path.basename(self.path)[: -len(".map")]

    @property
    def sources(self) -> List[str]:
        self._ensure()
        return self._sources_clean

    @property
    def has_content(self) -> bool:
        self._ensure()
        return any(self._content_lines)

    # -- 解码 -------------------------------------------------------------
    def _ensure_decoded(self) -> None:
        if self._decoded is not None:
            return
        self._ensure()
        mappings = (self._data or {}).get("mappings") or ""
        self._decoded = decode_mappings(mappings)
        self._cols = [[s[0] for s in segments] for segments in self._decoded]

    def lookup(self, line: int, col: int = 0) -> Optional[Dict[str, Any]]:
        """按生成代码的 1-based 行 / 0-based 列找原始位置。"""
        self._ensure_decoded()
        assert self._decoded is not None
        idx = line - 1
        if idx < 0 or idx >= len(self._decoded):
            return None
        segments = self._decoded[idx]
        if not segments:
            return None
        cols = self._cols[idx]
        pos = bisect.bisect_right(cols, col) - 1
        if pos < 0:
            pos = 0
        gen_col, src, src_line, src_col, name_idx = segments[pos]
        if src is None or src < 0 or src >= len(self._sources_clean):
            return None
        return {
            "source": self._sources_clean[src],
            "source_index": src,
            "line": (src_line or 0) + 1,
            "column": (src_col or 0) + 1,
            "name": self._names[name_idx] if name_idx is not None and name_idx < len(self._names) else "",
        }

    def source_line(self, src_index: int, line_no: int) -> str:
        """取 sourcesContent 里第 line_no 行（1-based）。"""
        self._ensure()
        if src_index < 0 or src_index >= len(self._content_lines):
            return ""
        lines = self._content_lines[src_index]
        if line_no - 1 < 0 or line_no - 1 >= len(lines):
            return ""
        return lines[line_no - 1]

    def source_context(self, src_index: int, line_no: int, context: int = 3) -> List[Tuple[int, str, bool]]:
        """取某行前后若干行，返回 [(行号, 文本, 是否命中行)]。"""
        self._ensure()
        if src_index < 0 or src_index >= len(self._content_lines):
            return []
        lines = self._content_lines[src_index]
        lo = max(1, line_no - context)
        hi = min(len(lines), line_no + context)
        return [(n, lines[n - 1], n == line_no) for n in range(lo, hi + 1)]

    def find_source_index(self, wanted: str) -> int:
        """按路径后缀把 sourcemap 里的源文件对上号，找不到返回 -1。"""
        wanted = clean_source_path(wanted).lstrip("/")
        if not wanted:
            return -1
        best = -1
        best_len = -1
        for i, src in enumerate(self.sources):
            s = src.lstrip("/")
            if s == wanted or s.endswith("/" + wanted):
                if len(s) > best_len:
                    best, best_len = i, len(s)
        if best >= 0:
            return best
        base = wanted.rsplit("/", 1)[-1]
        for i, src in enumerate(self.sources):
            if src.rsplit("/", 1)[-1] == base:
                return i
        return -1

    def basename_matches(self, basename: str) -> int:
        """这个 map 里有多少个源文件的文件名等于 basename。"""
        return sum(1 for src in self.sources if src.rsplit("/", 1)[-1] == basename)


# ---------------------------------------------------------------------------
# 底包索引
# ---------------------------------------------------------------------------

class BundleIndex:
    """扫描一个底包目录，收集里面所有 *.js.map。"""

    def __init__(self, root: str, progress=None):
        self.root = os.path.abspath(root)
        self.progress = progress or (lambda _msg: None)
        self.maps: List[SourceMap] = []
        self._scan()

    def _scan(self) -> None:
        if not os.path.isdir(self.root):
            raise ValueError("底包目录不存在：%s" % self.root)
        paths: List[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
            for name in filenames:
                if name.endswith(".js.map") or name.endswith(".mjs.map"):
                    paths.append(os.path.join(dirpath, name))
        paths.sort()
        for p in paths:
            self.progress("载入 sourcemap：%s" % os.path.relpath(p, self.root))
            self.maps.append(SourceMap(p, self.root))
        if not self.maps:
            raise ValueError("在 %s 下没找到任何 *.js.map，确认这是解压后的底包目录" % self.root)

    def stats(self) -> Dict[str, Any]:
        total_sources = 0
        with_content = 0
        for m in self.maps:
            n = len(m.sources)
            total_sources += n
            if m.has_content:
                with_content += 1
        return {
            "root": self.root,
            "maps": len(self.maps),
            "sources": total_sources,
            "maps_with_content": with_content,
        }

    # -- JS 帧 -> 源码帧 ---------------------------------------------------
    def _candidates(self, frame_file: str) -> List[SourceMap]:
        """挑出可能是这一帧来源的 sourcemap。"""
        base = os.path.basename(frame_file)
        stem = base.rsplit(".", 1)[0]
        frame_dir = os.path.dirname(frame_file).lstrip("./")

        scored: List[Tuple[int, SourceMap]] = []
        for m in self.maps:
            score = 0
            bundle_base = os.path.basename(m.bundle_rel)  # index.js.map
            bundle_dir = os.path.dirname(m.bundle_rel)
            declared = os.path.basename(m.js_file)  # all.js / cc.js

            if declared == base:
                score += 6
            if bundle_base[: -len(".map")] == base:
                score += 6
            if os.path.basename(bundle_dir) and os.path.basename(bundle_dir) in frame_file:
                score += 3
            if frame_dir and bundle_dir.endswith(frame_dir):
                score += 2
            if m.bundle_rel.startswith(frame_file.rsplit(".", 1)[0]):
                score += 3
            if stem == "all":
                score += 1
            if score:
                scored.append((score, m))

        if not scored:
            # 没有任何 map 名/目录对得上（比如纯平台层的 WAGame.js），
            # 此时不该拿别的 bundle 的 mappings 硬猜，宁可留空
            return []
        scored.sort(key=lambda x: -x[0])
        top = scored[0][0]
        return [m for s, m in scored if s >= max(1, top - 4)]

    def resolve_js_frame(self, frame_file: str, line: int, col: Optional[int],
                         func: str = "") -> Optional[Dict[str, Any]]:
        """把一个压缩 JS 帧映射回源码位置。多个候选时按函数名打分挑最像的。"""
        candidates = self._candidates(frame_file)
        best: Optional[Dict[str, Any]] = None
        best_score = -1

        for m in candidates:
            hit = m.lookup(line, col or 0)
            if not hit:
                continue
            score = 0
            if func and hit["name"] and hit["name"] == func:
                score += 10
            elif func and hit["name"] and func.endswith("." + hit["name"]):
                score += 8
            if os.path.basename(m.js_file) == os.path.basename(frame_file):
                score += 3
            if m.has_content:
                score += 1
            if score > best_score:
                best_score = score
                hit = dict(hit)
                hit["map"] = m
                best = hit
        return best

    # -- 已经是 .ts 的帧 ---------------------------------------------------
    def resolve_ts_frame(self, frame_file: str, line: int,
                         prefer: Optional[List["SourceMap"]] = None) -> Optional[Dict[str, Any]]:
        """微信开发者工具有时已经映射成 .ts 了，这里补出对应的源码行。

        `prefer` 是相邻帧已经确定的 sourcemap。像 `index.ts` 这种裸文件名
        在底包里会撞名（zdev 一个、Cocos warmup 一个），优先在相邻帧所在的
        map 里找，找不到再全局找。
        """
        wanted = clean_source_path(frame_file)
        w = wanted.lstrip("/")
        base = w.rsplit("/", 1)[-1]

        def pick(maps: List[SourceMap]) -> Optional[Dict[str, Any]]:
            best = None
            best_len = -1
            for m in maps:
                for i, src in enumerate(m.sources):
                    s = src.lstrip("/")
                    if s == w or s.endswith("/" + w):
                        if len(s) > best_len:
                            best_len = len(s)
                            best = {"source": src, "source_index": i, "line": line,
                                    "column": 0, "name": "", "map": m}
            return best

        # 1) 先在相邻帧的 map 里找
        if prefer:
            hit = pick(prefer)
            if hit:
                return hit

        # 2) 带目录的路径走到这里就是全局也没找到，别硬猜
        if "/" in w:
            return None

        # 3) 裸文件名只有「全局唯一」时才敢用：底包里 2629 个源文件，
        #    index.ts 这种名字到处都有，靠路径长度猜必定猜错
        holders = [m for m in self.maps if m.basename_matches(base)]
        if len(holders) == 1:
            return pick(holders)
        return None


# ---------------------------------------------------------------------------
# 堆栈解析
# ---------------------------------------------------------------------------

@dataclass
class Frame:
    raw: str
    func: str = ""
    file: str = ""
    line: int = 0
    column: Optional[int] = None

    def __str__(self) -> str:
        loc = "%s:%d" % (self.file, self.line)
        if self.column is not None:
            loc += ":%d" % self.column
        name = self.func or "(anonymous)"
        return "%s @ %s" % (name, loc)


# 微信开发者工具格式：  func    @    file:line:col
_WX_RE = re.compile(r"^\s*(?P<func>.*?)\s*@\s*(?P<file>[^@\s]+?):(?P<line>\d+)(?::(?P<col>\d+))?\s*$")
# Chrome / V8 格式：   at func (file:line:col)   或   at file:line:col
_V8_RE = re.compile(r"^\s*at\s+(?:(?P<func>[^(]+?)\s+\()?(?P<file>[^()\s]+?):(?P<line>\d+)(?::(?P<col>\d+))?\)?\s*$")
# Safari 格式：        func@file:line:col
_SAFARI_RE = re.compile(r"^\s*(?P<func>[^\s@]*?)@(?P<file>[^@\s]+?):(?P<line>\d+)(?::(?P<col>\d+))?\s*$")

# 堆栈开头的「错误描述行」，例如 xxx is not defined
_ERROR_HEAD_RE = re.compile(r"^\s*(?:Uncaught\s+)?[A-Za-z_$][\w$.]*(?:Error|Exception)?\s*:")


def parse_stack(text: str) -> Tuple[str, List[Frame]]:
    """把整段堆栈文本切成 (错误标题, 帧列表)。"""
    frames: List[Frame] = []
    headline = ""
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("(env:") or stripped.startswith("(lib:"):
            continue
        m = _WX_RE.match(raw) or _V8_RE.match(raw) or _SAFARI_RE.match(raw)
        if not m:
            if not headline and not frames:
                headline = stripped
                continue
            # 兼容 "(anonymous)    @    WAGame.js:1" 这种没列号的
            m2 = re.match(r"^\s*(?P<func>.*?)\s*@\s*(?P<file>[^@\s]+?):(?P<line>\d+)\s*$", raw)
            if not m2:
                continue
            m = m2
        d = m.groupdict()
        func = (d.get("func") or "").strip()
        if func.startswith("at "):
            func = func[3:].strip()
        frames.append(Frame(
            raw=raw.rstrip(),
            func=func,
            file=(d.get("file") or "").strip(),
            line=int(d.get("line") or 0),
            column=int(d["col"]) if d.get("col") else None,
        ))
    return headline, frames


# ---------------------------------------------------------------------------
# 解析结果
# ---------------------------------------------------------------------------

@dataclass
class ResolvedFrame:
    index: int
    frame: Frame
    kind: str = "unknown"          # sourcemap / ts / unknown
    source: str = ""               # 源码完整路径
    source_index: int = -1
    line: int = 0
    column: int = 0
    name: str = ""
    text: str = ""                 # 该行源码
    context: List[Tuple[int, str, bool]] = field(default_factory=list)
    category: str = ""             # 业务 / 框架 / 平台 / 转译 / 未知
    map_rel: str = ""
    note: str = ""
    git: Dict[str, Any] = field(default_factory=dict)

    @property
    def location(self) -> str:
        if self.kind == "unknown":
            loc = "%s:%d" % (self.frame.file, self.frame.line)
            if self.frame.column is not None:
                loc += ":%d" % self.frame.column
            return loc
        loc = "%s:%d" % (short_path(self.source, 2), self.line)
        if self.column:
            loc += ":%d" % self.column
        return loc

    @property
    def display_func(self) -> str:
        return self.name or self.frame.func or "(anonymous)"


# 引擎 / 平台 / 转译层特征
_PLATFORM_HINTS = (
    "WAGame.js", "WAGameSubContext.js", "gamePage.html", "game.js",
    "web-adapter", "engine-adapter", "first-screen",
)
_TRANSPILE_HINTS = (
    "rollupPluginModLoBabelHelpers", "_ts_generator", "@babel", "helpers.js",
    "polyfills", "system.bundle", "import-map",
)


def classify(resolved: ResolvedFrame, map_is_engine: bool) -> str:
    if resolved.kind == "unknown":
        f = resolved.frame.file
        if any(h in f for h in _PLATFORM_HINTS):
            return "平台"
        if any(h in f for h in _TRANSPILE_HINTS):
            return "转译"
        return "未知"
    src = resolved.source
    if any(h in src for h in _TRANSPILE_HINTS):
        return "转译"
    if map_is_engine or "/cocos-js/" in ("/" + resolved.map_rel) or src.endswith(".mjs"):
        return "框架"
    if "/assets/" in src and "/tsscript/" in src:
        return "框架"
    return "业务"


def resolve_stack(index: BundleIndex, text: str, context: int = 3,
                  progress=None) -> Tuple[str, List[ResolvedFrame]]:
    """整段堆栈 -> 逐帧源码位置。"""
    log = progress or (lambda _m: None)
    headline, frames = parse_stack(text)
    out: List[ResolvedFrame] = []

    # 第一遍：把所有帧都过一遍，记下每帧匹配到的 map
    for i, fr in enumerate(frames, 1):
        rf = ResolvedFrame(index=i, frame=fr)
        base = os.path.basename(fr.file)
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""

        if ext in ("ts", "tsx"):
            rf.kind = "ts"
            # 带目录的路径（debugToolLogic/DebugToolBase.ts）足够唯一，直接全局定位，
            # 同时也给后面的裸文件名帧当参照点
            if "/" in fr.file.replace("\\", "/"):
                hit = index.resolve_ts_frame(fr.file, fr.line)
                if hit:
                    rf.source = hit["source"]
                    rf.source_index = hit["source_index"]
                    rf.column = 0
                    rf.map_rel = hit["map"].bundle_rel
                    rf.text = hit["map"].source_line(hit["source_index"], fr.line)
                    rf.context = hit["map"].source_context(hit["source_index"], fr.line, context)
                else:
                    rf.kind = "unknown"
                    rf.note = "底包里找不到这个源文件"
            # 裸文件名（index.ts）留到第二遍，用相邻帧的 map 消歧
            rf.line = fr.line
        elif ext in ("js", "mjs", "cjs", "vue"):
            log("解析帧 #%d：%s:%d" % (i, base, fr.line))
            hit = index.resolve_js_frame(fr.file, fr.line, fr.column, fr.func)
            if hit:
                rf.kind = "sourcemap"
                rf.source = hit["source"]
                rf.source_index = hit["source_index"]
                rf.line = hit["line"]
                rf.column = hit["column"]
                rf.name = hit["name"]
                rf.map_rel = hit["map"].bundle_rel
                rf.text = hit["map"].source_line(hit["source_index"], rf.line)
                rf.context = hit["map"].source_context(hit["source_index"], rf.line, context)
        out.append(rf)

    # 第二遍：定位裸文件名 TS 帧（index.ts、DebugToolControl.ts）。
    # 它们的消歧依赖相邻帧，而相邻帧本身可能也要等下一轮才定位好，
    # 所以反复扫，直到一轮下来没有任何进展。
    map_by_rel = {m.bundle_rel: m for m in index.maps}
    pending = True
    while pending:
        pending = False
        for pos, rf in enumerate(out):
            if rf.kind != "ts" or rf.map_rel:
                continue
            prefer: List[SourceMap] = []
            seen = set()
            for step in (-1, 1, -2, 2, -3, 3):
                j = pos + step
                if 0 <= j < len(out):
                    rel = out[j].map_rel
                    if rel and rel not in seen:
                        seen.add(rel)
                        prefer.append(map_by_rel[rel])
            hit = index.resolve_ts_frame(rf.frame.file, rf.frame.line, prefer or None)
            if not hit:
                continue
            rf.source = hit["source"]
            rf.source_index = hit["source_index"]
            rf.column = 0
            rf.map_rel = hit["map"].bundle_rel
            rf.text = hit["map"].source_line(hit["source_index"], rf.line)
            rf.context = hit["map"].source_context(hit["source_index"], rf.line, context)
            pending = True

    for rf in out:
        if rf.kind == "ts" and not rf.map_rel:
            rf.kind = "unknown"
            rf.note = "源码文件名在底包里不唯一或不存在，无法定位"

    for rf in out:
        rf.category = classify(rf, "/cocos-js/" in ("/" + rf.map_rel))
        if rf.kind != "unknown" and not rf.text:
            rf.note = "该 sourcemap 未内嵌 sourcesContent，拿不到源码行"

    return headline, out


# ---------------------------------------------------------------------------
# Git 仓库联动（可选）
# ---------------------------------------------------------------------------

class GitRepo:
    """把 sourcemap 里的源码路径对上 git 仓库里的真实文件，并取 blame。"""

    def __init__(self, path: str, tag: str = ""):
        self.path = os.path.abspath(path)
        self.tag = tag
        self._tree: Optional[List[str]] = None
        self._ok = os.path.isdir(os.path.join(self.path, ".git"))
        self.error = "" if self._ok else "目录里没有 .git：%s" % self.path

    def _run(self, args: List[str], timeout: int = 30) -> str:
        try:
            out = subprocess.run(
                ["git", "-C", self.path] + args,
                capture_output=True, text=True, timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.error = "git 调用失败：%s" % exc
            return ""
        if out.returncode != 0:
            self.error = out.stderr.strip() or self.error
            return ""
        return out.stdout

    def rev(self) -> str:
        if self.tag:
            sha = self._run(["rev-parse", "--verify", "%s^{commit}" % self.tag]).strip()
            return sha or self.tag
        return self._run(["rev-parse", "HEAD"]).strip()

    def tree(self) -> List[str]:
        if self._tree is not None:
            return self._tree
        rev = self.rev() or "HEAD"
        raw = self._run(["ls-tree", "-r", "--name-only", rev])
        self._tree = [ln for ln in raw.splitlines() if ln]
        return self._tree

    def locate(self, source_path: str) -> str:
        """把 sourcemap 里的绝对路径对到仓库相对路径。"""
        cleaned = clean_source_path(source_path).lstrip("/")
        tree = self.tree()
        if not tree:
            return ""
        best = ""
        for rel in tree:
            if cleaned == rel or cleaned.endswith("/" + rel):
                if len(rel) > len(best):
                    best = rel
        if best:
            return best
        base = cleaned.rsplit("/", 1)[-1]
        hits = [r for r in tree if r.rsplit("/", 1)[-1] == base]
        if len(hits) == 1:
            return hits[0]
        return ""

    def blame(self, rel_path: str, line: int) -> Dict[str, Any]:
        rev = self.rev() or "HEAD"
        raw = self._run([
            "blame", "-L", "%d,%d" % (line, line), "--porcelain", rev, "--", rel_path,
        ])
        if not raw:
            return {}
        info: Dict[str, Any] = {}
        for ln in raw.splitlines():
            if ln.startswith("author "):
                info["author"] = ln[7:].strip()
            elif ln.startswith("author-time "):
                info["time"] = ln[12:].strip()
            elif ln.startswith("summary "):
                info["summary"] = ln[8:].strip()
            elif ln.startswith("\t"):
                break
        if raw:
            info["sha"] = raw.split(" ", 1)[0].strip()
        return info


def enrich_with_git(frames: Iterable[ResolvedFrame], repo: Optional[GitRepo]) -> None:
    if not repo or not repo._ok:
        return
    for rf in frames:
        if rf.kind == "unknown" or not rf.source:
            continue
        rel = repo.locate(rf.source)
        if not rel:
            continue
        rf.git = {"path": rel}
        rf.git.update(repo.blame(rel, rf.line))


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

_CATEGORY_ORDER = {"业务": 0, "框架": 1, "平台": 2, "转译": 3, "未知": 4}


def format_report(headline: str, frames: List[ResolvedFrame], *,
                  show_all: bool = False, show_context: bool = True,
                  info: Optional[Dict[str, Any]] = None) -> str:
    """把解析结果渲染成可读文本。"""
    out: List[str] = []
    bar = "─" * 78

    if info:
        out.append("底包：%s" % info.get("root", ""))
        out.append("sourcemap：%d 个，源文件 %d 个（其中 %d 个内嵌了源码）" % (
            info.get("maps", 0), info.get("sources", 0), info.get("maps_with_content", 0)))
    if headline:
        out.append("错误：%s" % headline)
    out.append("")

    biz = [f for f in frames if f.category == "业务"]
    other = [f for f in frames if f.category != "业务"]

    out.append("摘要：%d 帧，其中业务帧 %d 个" % (len(frames), len(biz)))
    if biz:
        out.append("业务调用链（由内向外）：")
        for f in biz:
            out.append("  #%-2d %-46s %s" % (f.index, f.location, f.display_func))
    out.append("")

    # 业务帧详情
    if biz:
        out.append(bar)
        out.append("业务帧详情")
        out.append(bar)
        for f in biz:
            out.append("")
            out.append("帧 #%d  %s" % (f.index, f.location))
            out.append("  函数：%s" % f.display_func)
            if f.kind == "ts":
                out.append("  来源：微信已映射（%s）" % f.map_rel)
            elif f.kind == "sourcemap":
                out.append("  来源：%s" % f.map_rel)
            out.append("  文件：%s" % f.source)
            if f.git.get("path"):
                extra = ""
                if f.git.get("author"):
                    extra = "  作者 %s" % f.git["author"]
                if f.git.get("summary"):
                    extra += "  提交 %s" % f.git["summary"]
                out.append("  git：%s%s" % (f.git["path"], extra))
            if f.text.strip():
                if show_context and f.context:
                    out.append("  上下文：")
                    for n, t, hit in f.context:
                        mark = ">>" if hit else "  "
                        out.append("    %s %4d | %s" % (mark, n, t.rstrip()))
                else:
                    out.append("  源码：%d | %s" % (f.line, f.text.rstrip()))
            elif f.note:
                out.append("  源码：（%s）" % f.note)
            out.append("  原始帧：%s" % f.frame.raw.strip())

    # 其它帧
    if other:
        out.append("")
        out.append(bar)
        out.append("非业务帧（默认折叠）" if not show_all else "非业务帧")
        out.append(bar)
        if show_all:
            for f in other:
                out.append("  #%-2d [%s] %-40s %s" % (f.index, f.category, f.location, f.display_func))
        else:
            grouped: Dict[str, List[ResolvedFrame]] = {}
            for f in other:
                grouped.setdefault(f.category, []).append(f)
            for cat in sorted(grouped, key=lambda c: _CATEGORY_ORDER.get(c, 9)):
                items = grouped[cat]
                names = []
                for f in items:
                    n = f.location
                    if n not in names:
                        names.append(n)
                head = "  [%s] %d 帧" % (cat, len(items))
                if names:
                    shown = "、".join(names[:4])
                    if len(names) > 4:
                        shown += " 等"
                    head += "：" + shown
                out.append(head)

    return "\n".join(out)


def report_json(headline: str, frames: List[ResolvedFrame]) -> Dict[str, Any]:
    return {
        "headline": headline,
        "frames": [
            {
                "index": f.index,
                "category": f.category,
                "kind": f.kind,
                "function": f.display_func,
                "source": f.source,
                "line": f.line,
                "column": f.column,
                "text": f.text,
                "map": f.map_rel,
                "raw": f.frame.raw.strip(),
                "git": f.git,
            }
            for f in frames
        ],
    }

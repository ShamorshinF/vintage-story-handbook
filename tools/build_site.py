#!/usr/bin/env python3
"""Vintage Story handbook -> static web site data.

Reads the game's assets plus every mod of the last game session and writes
docs/data/handbook.json (guides + index) and docs/data/items-*.json (item and
block pages) for the static site in docs/.

Nothing from a mod is executed: zips are only read with zipfile, DLLs are only
scanned as bytes. Run with:

    python -I tools/build_site.py

See README.md for the options.
"""
import argparse, bisect, copy, datetime, html, itertools, json, os, re, shutil, sys, zipfile
from urllib.parse import quote

VANILLA_FOLDERS = ("game", "survival", "creative")   # all three use the asset domain "game"
VANILLA_IDS = ("game", "creative", "survival")


# ----------------------------------------------------------------------------------------------
# Lenient JSON (the game reads with Newtonsoft: comments, unquoted keys, single quotes,
# trailing commas, raw line breaks inside strings)
# ----------------------------------------------------------------------------------------------
class LenientJson:
    WS = " \t\r\n﻿ "
    BARE = re.compile(r"[^\s,:\]\}\[\{\"']+")
    NUM = re.compile(r"[-+]?(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)")
    ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "/": "/", "\\": "\\", '"': '"', "'": "'",
           "0": "\0"}

    def __init__(self, text):
        self.s = text
        self.i = 0
        self.n = len(text)

    def err(self, msg):
        line = self.s.count("\n", 0, self.i) + 1
        raise ValueError(f"{msg} at line {line}")

    def skip(self):
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in self.WS:
                self.i += 1
            elif c == "/" and s[self.i + 1:self.i + 2] == "/":
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j + 1
            elif c == "/" and s[self.i + 1:self.i + 2] == "*":
                j = s.find("*/", self.i + 2)
                self.i = n if j < 0 else j + 2
            else:
                break

    def value(self):
        self.skip()
        if self.i >= self.n:
            self.err("unexpected end")
        c = self.s[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c in "\"'":
            return self.string()
        m = self.NUM.match(self.s, self.i)
        if m and m.end() > self.i:
            nxt = self.s[m.end():m.end() + 1]
            if nxt == "" or nxt in self.WS or nxt in ",]}/":
                self.i = m.end()
                t = m.group(0)
                try:
                    if t.lower().lstrip("+-").startswith("0x"):
                        return int(t, 16)
                    return int(t) if re.fullmatch(r"[-+]?\d+", t) else float(t)
                except ValueError:
                    return t
        m = self.BARE.match(self.s, self.i)
        if not m:
            self.err(f"unexpected character {c!r}")
        self.i = m.end()
        w = m.group(0)
        lw = w.lower()
        if lw == "true":
            return True
        if lw == "false":
            return False
        if lw in ("null", "undefined"):
            return None
        return w

    def string(self):
        s = self.s
        q = s[self.i]
        self.i += 1
        out = []
        while True:
            if self.i >= self.n:
                self.err("unterminated string")
            c = s[self.i]
            if c == q:
                self.i += 1
                return "".join(out)
            if c == "\\":
                self.i += 1
                e = s[self.i:self.i + 1]
                if e == "u":
                    try:
                        out.append(chr(int(s[self.i + 1:self.i + 5], 16)))
                        self.i += 5
                    except ValueError:
                        out.append("u")
                        self.i += 1
                elif e == "\n":
                    self.i += 1
                else:
                    out.append(self.ESC.get(e, e))
                    self.i += 1
            else:
                j = self.i
                while j < self.n and s[j] != q and s[j] != "\\":
                    j += 1
                out.append(s[self.i:j])
                self.i = j

    def key(self):
        self.skip()
        c = self.s[self.i:self.i + 1]
        if c in ('"', "'"):
            return self.string()
        m = self.BARE.match(self.s, self.i)
        if not m:
            self.err("bad object key")
        self.i = m.end()
        return m.group(0)

    def obj(self):
        self.i += 1
        d = {}
        while True:
            self.skip()
            if self.i >= self.n:
                return d            # the game also accepts a file whose last brackets are missing
            c = self.s[self.i]
            if c == "}":
                self.i += 1
                return d
            if c == ",":
                self.i += 1
                continue
            k = self.key()
            self.skip()
            if self.s[self.i:self.i + 1] != ":":
                self.err(f"expected ':' after key {k!r}")
            self.i += 1
            d[k] = self.value()

    def arr(self):
        self.i += 1
        a = []
        while True:
            self.skip()
            if self.i >= self.n:
                return a
            c = self.s[self.i]
            if c == "]":
                self.i += 1
                return a
            if c == ",":
                self.i += 1
                continue
            a.append(self.value())


def decode_bytes(b):
    if b[:3] == b"\xef\xbb\xbf":
        b = b[3:]
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", "replace")


def loads_lenient(data):
    text = decode_bytes(data) if isinstance(data, (bytes, bytearray)) else data
    try:
        return json.loads(text)
    except ValueError:
        pass
    p = LenientJson(text)
    p.skip()
    if p.i >= p.n:
        return None          # empty, or everything commented out
    return p.value()


def lower_keys(d):
    return {str(k).lower(): v for k, v in d.items()} if isinstance(d, dict) else {}


# ----------------------------------------------------------------------------------------------
# Asset sources
# ----------------------------------------------------------------------------------------------
class Source:
    """One asset origin: the vanilla asset folders, a mod zip or a mod folder."""
    def __init__(self):
        self.modid = ""
        self.name = ""
        self.version = ""
        self.label = ""        # file name shown in the report (never a full path)
        self.deps = []
        self.assets = {}       # "domain:path/in/domain" (lower case) -> handle
        self.dlls = []
        self.side = ""

    def keys_under(self, prefix_path, suffix=".json"):
        out = []
        for k in self.assets:
            p = k.split(":", 1)[1]
            if p.startswith(prefix_path) and p.endswith(suffix):
                out.append(k)
        return out


class DirSource(Source):
    def __init__(self, folder=None, label=None):
        super().__init__()
        self.folder = folder
        self.label = label or (os.path.basename(folder.rstrip("\\/")) if folder else "")
        if folder:
            a = os.path.join(folder, "assets")
            if os.path.isdir(a):
                for dom in os.listdir(a):
                    if os.path.isdir(os.path.join(a, dom)):
                        self.walk(os.path.join(a, dom), dom.lower())
            for root, _d, files in os.walk(folder):
                for f in files:
                    if f.lower().endswith(".dll"):
                        self.dlls.append(os.path.join(root, f))

    def walk(self, base, domain):
        for root, _d, files in os.walk(base):
            rel = os.path.relpath(root, base).replace("\\", "/")
            rel = "" if rel == "." else rel + "/"
            for f in files:
                self.assets[f"{domain}:{(rel + f).lower()}"] = os.path.join(root, f)

    def read(self, key):
        with open(self.assets[key], "rb") as fh:
            return fh.read()

    def read_dll(self, handle):
        with open(handle, "rb") as fh:
            return fh.read()

    def read_modinfo(self):
        p = os.path.join(self.folder, "modinfo.json")
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                return fh.read()
        return None


class ZipSource(Source):
    def __init__(self, path):
        super().__init__()
        self.label = os.path.basename(path)
        self.zf = zipfile.ZipFile(path)
        names = [n for n in self.zf.namelist() if not n.endswith("/")]
        norm = {n: n.replace("\\", "/") for n in names}
        # the mod root is where modinfo.json sits (normally the zip root)
        roots = sorted((v[:-len("modinfo.json")] for v in norm.values()
                        if v.lower().endswith("modinfo.json") and "/assets/" not in "/" + v.lower()), key=len)
        self.root = roots[0] if roots else ""
        self.modinfo_name = None
        for n, v in norm.items():
            if not v.startswith(self.root):
                continue
            rel = v[len(self.root):]
            low = rel.lower()
            if low == "modinfo.json":
                self.modinfo_name = n
            elif low.startswith("assets/"):
                parts = rel.split("/", 2)
                if len(parts) == 3:
                    self.assets[f"{parts[1].lower()}:{parts[2].lower()}"] = n
            elif low.endswith(".dll"):
                self.dlls.append(n)

    def read(self, key):
        return self.zf.read(self.assets[key])

    def read_dll(self, handle):
        return self.zf.read(handle)

    def read_modinfo(self):
        return self.zf.read(self.modinfo_name) if self.modinfo_name else None


def parse_version(v):
    """Sortable key for mod versions such as 1.2.3, 2.0.0-dev.28, 1.0.0-pre.8."""
    v = str(v or "0").strip().lstrip("vV")
    main, _, pre = v.partition("-")
    nums = [int(x) if x.isdigit() else 0 for x in main.split(".")]
    nums += [0] * (4 - len(nums))
    if not pre:
        return (tuple(nums), 1, ())
    parts = tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre.split("."))
    return (tuple(nums), 0, parts)


def load_mod_source(path, problems):
    try:
        if os.path.isdir(path):
            src = DirSource(path)
        elif path.lower().endswith(".zip"):
            src = ZipSource(path)
        else:
            return None
        raw = src.read_modinfo()
        if raw is None:
            if os.path.isdir(path):
                return None
            problems.append(f"{src.label}: no modinfo.json, skipped")
            return None
        info = lower_keys(loads_lenient(raw))
        src.name = str(info.get("name") or "").strip()
        src.modid = str(info.get("modid") or re.sub(r"[^a-z0-9]", "", src.name.lower())).lower()
        src.name = src.name or src.modid
        src.version = str(info.get("version") or "")
        src.side = str(info.get("side") or "universal").lower()
        deps = info.get("dependencies") or {}
        src.deps = [str(k).lower() for k in deps] if isinstance(deps, dict) else []
        return src
    except Exception as e:                       # a broken zip must not stop the build
        problems.append(f"{os.path.basename(path)}: could not be read ({type(e).__name__}: {e})")
        return None


# ----------------------------------------------------------------------------------------------
# The client log: which folders were searched and which mods actually loaded, in load order
# ----------------------------------------------------------------------------------------------
def read_client_log(path):
    """Returns (search paths, mod ids in load order) of the last session in the log, or ([], [])."""
    paths, order = [], []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return paths, order
    cur = None
    for line in lines:
        if "Will search the following paths for mods:" in line:
            cur = []
            continue
        if cur is not None:
            m = re.match(r"^\S+ \S+ \[Notification\]\s{2,}(\S.*?)\s*$", line)
            if m:
                cur.append(m.group(1))
                continue
            paths, cur = cur, None
        m = re.search(r"Mods, sorted by dependency:\s*(.+)$", line)
        if m:
            order = [x.strip().lower() for x in m.group(1).split(",") if x.strip()]
    return paths, order


def expand(p):
    # the log writes %appdata% in lower case
    p = re.sub(r"%([A-Za-z_]+)%", lambda m: "%" + m.group(1).upper() + "%", p)
    return os.path.normpath(os.path.expandvars(p))


# ----------------------------------------------------------------------------------------------
# Language entries (port of TranslationService key rules)
# ----------------------------------------------------------------------------------------------
class Lang:
    def __init__(self):
        self.exact = {}
        self.wild = {}      # prefix -> value   (keys with one trailing *)
        self.regex = {}     # key -> (compiled, value)
        self.wild_by_len = None

    @staticmethod
    def with_domain(key, domain="game"):
        return key if ":" in key else f"{domain}:{key}"

    def load(self, entries, domain):
        for k, v in entries.items():
            if not isinstance(v, str):
                continue
            key = self.with_domain(str(k), domain)
            stars = key.count("*")
            if stars == 0:
                self.exact[key] = v
            elif stars == 1 and key.endswith("*"):
                self.wild[key[:-1]] = v
            else:
                try:
                    self.regex[key] = (re.compile("^" + re.escape(key).replace(r"\*", "(.*)") + "$"), v)
                except re.error:
                    pass
        self.wild_by_len = None

    def get(self, key):
        """Lang.Get with no arguments: the entry, or the key itself."""
        v = self.exact.get(self.with_domain(key))
        return unformat(v) if v is not None else key

    def get_if_exists(self, key):
        v = self.exact.get(self.with_domain(key))
        return unformat(v) if v is not None else None

    def matching(self, key):
        """Lang.GetMatchingIfExists: exact, then trailing-wildcard, then regex entries."""
        k = self.with_domain(key)
        v = self.exact.get(k)
        if v is not None:
            return v
        if self.wild_by_len is None:
            self.wild_by_len = sorted(self.wild.items(), key=lambda kv: -len(kv[0]))
        for pre, val in self.wild_by_len:
            if k.startswith(pre):
                return val
        for rx, val in self.regex.values():
            if rx.match(k):
                return val
        return None


def unformat(v):
    # string.Format with no arguments only turns {{ and }} into single braces
    if "{{" in v or "}}" in v:
        rest = v.replace("{{", "").replace("}}", "")
        if "{" not in rest and "}" not in rest:
            return v.replace("{{", "{").replace("}}", "}")
    return v


# ----------------------------------------------------------------------------------------------
# VTML (the game's rich text markup) -> safe HTML
# ----------------------------------------------------------------------------------------------
class Tag:
    __slots__ = ("name", "attrs", "children")

    def __init__(self, name, attrs):
        self.name, self.attrs, self.children = name, attrs, []

    def content_text(self):
        return "".join(c if isinstance(c, str) else c.content_text() for c in self.children)


def parse_tag(tag):
    """Port of VtmlParser.parseTagAttributes."""
    name, attrs = "", {}
    state, quote_ch, key, val = "name", "", "", ""
    for i, ch in enumerate(tag):
        ws = ch in " \t\r\n"
        if state == "name":
            if ws:
                state = "seekkey"
            else:
                name += ch
        elif state == "seekkey":
            if not ws:
                key, state = ch, "key"
        elif state == "key":
            if ch == "=":
                state, val = "seekval", ""
            elif ws:
                attrs[key.lower()] = None
                state = "seekkey"
            else:
                key += ch
        elif state == "seekval":
            if not ws:
                if ch in "'\"":
                    state, quote_ch, val = "quoted", ch, ""
                else:
                    state, val = "val", ch
        elif state == "val":
            if ws:
                attrs[key.lower()] = val
                state = "seekkey"
            else:
                val += ch
        elif state == "quoted":
            if ch == quote_ch and tag[i - 1] != "\\":
                attrs[key.lower()] = val
                state = "seekkey"
            else:
                val += ch
    if state == "val":
        attrs[key.lower()] = val
    return Tag(name.lower(), attrs)


TAG_START = re.compile(r"[A-Za-z/]")


def tokenize(vtml):
    """Port of VtmlParser.Tokenize, but forgiving: a stray < or > stays visible as text."""
    root, stack = [], []
    text, tag, intag = "", "", False

    def add(node):
        (stack[-1].children if stack else root).append(node)

    def flush():
        nonlocal text
        if text:
            add(text.replace("&gt;", ">").replace("&lt;", "<").replace("&nbsp;", " ").replace("&amp;", "&"))
        text = ""

    n = len(vtml)
    for i, ch in enumerate(vtml):
        if ch == "<" and not intag:
            if i + 1 < n and TAG_START.match(vtml[i + 1]):
                intag = True
                flush()
            else:
                text += ch
        elif ch == ">" and intag:
            intag = False
            if tag[:1] == "/":
                if stack:
                    stack.pop()
            elif tag.strip().lower() == "br":
                add(Tag("br", {}))
            elif tag.endswith("/"):
                add(parse_tag(tag[:-1]))
            else:
                t = parse_tag(tag)
                add(t)
                if t.name != "br":
                    stack.append(t)
            tag = ""
        elif intag:
            tag += ch
        else:
            text += ch
    if intag:
        text += "<" + tag
    flush()
    return root


# Default key for the hotkey codes guides use. The game shows the player's own binding.
HOTKEYS = {
    "shift": "Shift", "ctrl": "Ctrl", "alt": "Alt", "tab": "Tab", "space": "Space", "esc": "Esc",
    "leftmouse": "Left mouse", "rightmouse": "Right mouse", "middlemouse": "Middle mouse",
    "primarymouse": "Left mouse", "secondarymouse": "Right mouse",
    "sneak": "Shift", "sprint": "Ctrl", "jump": "Space", "sitdown": "G",
    "walkforward": "W", "walkbackward": "S", "walkleft": "A", "walkright": "D",
    "inventorydialog": "E", "characterdialog": "C", "dropitem": "Q", "dropitems": "Ctrl+Q",
    "toolmodeselect": "F", "toolmode": "F", "handbook": "H", "selectionhandbook": "Shift+H",
    "worldmapdialog": "M", "worldmaphud": "F6", "coordinateshud": "Ctrl+V", "blockinfohud": "Ctrl+B",
    "chatdialog": "T", "beginchat": "T", "fliphandslots": "X", "pickblock": "Middle mouse",
    "zoomout": "Z", "escapemenudialog": "Esc", "togglehud": "F4", "screenshot": "F12",
    "ctrlclick": "Ctrl", "shiftclick": "Shift",
}

ICON_KEYS = {"leftmousebutton": "Left mouse", "rightmousebutton": "Right mouse", "middlemousebutton": "Middle mouse"}
COLOR_RX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
NAMED_COLORS = {"red", "green", "blue", "yellow", "orange", "white", "black", "gray", "grey", "purple", "pink",
                "brown", "cyan", "magenta", "gold", "lime", "aqua", "silver", "maroon", "olive", "navy", "teal",
                "lightblue", "lightgreen", "lightgray", "lightgrey", "darkred", "darkgreen", "darkblue", "darkgray",
                "darkgrey", "violet", "indigo", "crimson", "khaki", "salmon", "tan", "turquoise", "coral"}
BASE_FONT = 15.0       # CairoFont.WhiteSmallText, the handbook body size


def frag(s):
    return quote(s, safe="-_.~:|")


class Renderer:
    """Turns VTML into HTML. All text is escaped; only a fixed set of tags and styles is written."""
    def __init__(self, link_resolver, stack_namer, stats):
        self.resolve, self.stack_name, self.stats = link_resolver, stack_namer, stats

    def render(self, vtml):
        out = []
        self.many(tokenize(vtml), out)
        return "".join(out)

    def plain(self, vtml):
        """Search text: the visible words only."""
        out = []
        self.plain_many(tokenize(vtml), out)
        return re.sub(r"\s+", " ", "".join(out)).strip()

    def plain_many(self, toks, out):
        for t in toks:
            if isinstance(t, str):
                out.append(t)
            elif t.name == "br":
                out.append(" ")
            elif t.name in ("hk", "hotkey"):
                c = t.content_text().strip()
                out.append(" " + HOTKEYS.get(c.lower(), c) + " ")
            elif t.name == "itemstack":
                names = self.stack_names(t)
                out.append(" " + (names[0][1] if names else "") + " ")
            elif t.name == "icon":
                nm = (t.attrs.get("name") or t.content_text() or "").strip().lower()
                out.append(" " + ICON_KEYS.get(nm, "") + " ")
            else:
                self.plain_many(t.children, out)

    @staticmethod
    def text(s):
        s = html.escape(s, quote=False)
        return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")

    def many(self, toks, out):
        for t in toks:
            if isinstance(t, str):
                out.append(self.text(t))
            else:
                self.tag(t, out)

    def stack_names(self, t):
        code = t.attrs.get("code") or t.content_text()
        typ = (t.attrs.get("type") or "block").lower()
        names = []
        for c in (code or "").split("|"):
            c = c.strip()
            if c:
                names.append(self.stack_name("item" if typ == "item" else "block", c))
        return names        # [(page code or None, display name)]

    def count(self, bucket, key):
        d = self.stats.setdefault(bucket, {})
        d[key] = d.get(key, 0) + 1

    def tag(self, t, out):
        n = t.name
        self.count("tags", n)
        if n == "br":
            out.append("<br>")
        elif n in ("hk", "hotkey"):
            c = t.content_text().strip()
            if c:
                key = HOTKEYS.get(c.lower())
                if key is None:
                    self.count("hotkeys_unmapped", c)
                    key = c
                out.append(f'<kbd title="{html.escape(c, quote=True)}">{html.escape(key)}</kbd>')
        elif n == "i":
            out.append("<i>")
            self.many(t.children, out)
            out.append("</i>")
        elif n in ("strong", "b"):
            out.append("<strong>")
            self.many(t.children, out)
            out.append("</strong>")
        elif n == "code":
            out.append("<code>")
            self.many(t.children, out)
            out.append("</code>")
        elif n == "clear":
            out.append('<span class="clr"></span>')
        elif n == "a":
            out.append(self.link(t.attrs.get("href") or "", t.content_text()))
        elif n == "icon":
            name = (t.attrs.get("name") or t.content_text() or "").strip()
            low = name.lower()
            if low in ICON_KEYS:
                out.append(f"<kbd>{ICON_KEYS[low]}</kbd>")
            elif name:
                out.append(f'<span class="icon" title="icon: {html.escape(name, quote=True)}">&#9679;</span>')
        elif n == "itemstack":
            names = self.stack_names(t)
            if names:
                page, label = names[0]
                more = f' <span class="more">+{len(names) - 1}</span>' if len(names) > 1 else ""
                tip = html.escape(", ".join(x[1] for x in names), quote=True)
                inner = html.escape(label) + more
                if page:
                    out.append(f'<a class="stack" href="#p/{html.escape(frag(page), quote=True)}" '
                               f'title="{tip}">{inner}</a>')
                else:
                    out.append(f'<span class="stack" title="{tip}">{inner}</span>')
        elif n == "font":
            attrs = self.font(t.attrs)
            if attrs:
                out.append(f"<span{attrs}>")
                self.many(t.children, out)
                out.append("</span>")
            else:
                self.many(t.children, out)
        else:
            # not a handbook tag (the game shows nothing for these); keep the words readable
            self.count("unknown_tags", n)
            self.many(t.children, out)

    def font(self, a):
        styles, classes = [], []
        col = (a.get("color") or "").strip()
        if col and (COLOR_RX.match(col) or col.lower() in NAMED_COLORS):
            classes.append("fc")
            styles.append(f"--c:{col.lower()}")
        size = None
        try:
            if a.get("size"):
                size = float(a["size"]) / BASE_FONT
        except ValueError:
            pass
        sc = a.get("scale") or ""
        if sc.endswith("%"):
            try:
                size = float(sc[:-1]) / 100.0
            except ValueError:
                pass
        if size is not None and abs(size - 1.0) > 0.03:
            size = max(0.8, min(1.8, size))
            styles.append(f"font-size:{size:.2f}em")
            if size >= 1.2:
                classes.append("big")
        w = a.get("weight")
        if w is not None:
            styles.append("font-weight:" + ("700" if w == "bold" else "400"))
        al = (a.get("align") or "").lower()
        if al in ("center", "right", "left"):
            classes.append("blk")
            styles.append(f"text-align:{al}")
        try:
            if a.get("opacity"):
                styles.append(f"opacity:{max(0.4, min(1.0, float(a['opacity']))):.2f}")
        except ValueError:
            pass
        cls = f' class="{" ".join(classes)}"' if classes else ""
        style = f' style="{";".join(styles)}"' if styles else ""
        return cls + style

    def link(self, href, text):
        label = html.escape(text, quote=False).replace("\n", "<br>")
        href = (href or "").strip()
        low = href.lower()
        if low.startswith("handbook://"):
            code = href[len("handbook://"):].replace("\\", "")
            if code.startswith("tab-"):
                return f'<span class="ref">{label}</span>'
            target = self.resolve(code)
            if target:
                return f'<a href="#p/{html.escape(frag(target), quote=True)}">{label}</a>'
            self.count("dead_links", code)
            tip = html.escape(code, quote=True)
            return f'<span class="ref" title="Handbook page not in this copy: {tip}">{label}</span>'
        if low.startswith("handbooksearch://"):
            q = href[len("handbooksearch://"):]
            return f'<a href="#s/{html.escape(frag(q), quote=True)}">{label}</a>'
        if low.startswith("https://") or low.startswith("http://"):
            return (f'<a class="ext" href="{html.escape(href, quote=True)}" target="_blank" '
                    f'rel="noopener noreferrer">{label}</a>')
        self.count("other_links", low.split("://")[0][:20])
        return f'<span class="ref">{label}</span>'


# ----------------------------------------------------------------------------------------------
# Finding the game, the mods and their load order
# ----------------------------------------------------------------------------------------------
class World:
    """Everything the build reads: vanilla + active mods in load order, language, problems."""
    def __init__(self):
        self.sources = []
        self.lang = Lang()
        self.problems = []
        self.notes = []
        self.game_version = ""
        self.winner = {}        # asset key -> Source that provides it (later origins override)

    def read_json(self, key):
        src = self.winner[key]
        return loads_lenient(src.read(key))


def default_game_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.environ.get("VINTAGE_STORY"), os.path.join(here, "..", ".."), os.getcwd()):
        if cand and os.path.isdir(os.path.join(cand, "assets", "survival")):
            return os.path.normpath(cand)
    return None


def default_log():
    base = os.environ.get("APPDATA")
    return os.path.join(base, "VintagestoryData", "Logs", "client-main.log") if base else None


def toposort(by_id):
    order, seen = [], set()

    def visit(mid):
        if mid in seen or mid not in by_id:
            return
        seen.add(mid)
        for d in sorted(by_id[mid].deps):
            visit(d)
        order.append(mid)

    for mid in sorted(by_id):
        visit(mid)
    return order


def discover(args):
    w = World()
    game = args.game or default_game_dir()
    if not game or not os.path.isdir(os.path.join(game, "assets", "survival")):
        sys.exit("Game folder not found. Pass --game <folder that contains assets\\survival>.")
    van = DirSource()
    for f in VANILLA_FOLDERS:
        base = os.path.join(game, "assets", f)
        if os.path.isdir(base):
            van.walk(base, "game")
    van.modid, van.name, van.label = "game", "Vintage Story", "(game assets)"
    for f in os.listdir(os.path.join(game, "assets")):
        m = re.match(r"version-(.+)\.txt$", f)
        if m:
            van.version = w.game_version = m.group(1)

    log_paths, order = ([], [])
    log = None if args.no_log else (args.client_log or default_log())
    if log:
        log_paths, order = read_client_log(log)
    mod_dirs = args.mods or [expand(p) for p in log_paths]
    if not mod_dirs and os.environ.get("APPDATA"):
        mod_dirs = [os.path.join(os.environ["APPDATA"], "VintagestoryData", "Mods")]

    by_id, dups = {}, []
    for d in mod_dirs:
        if not os.path.isdir(d):
            continue
        for e in sorted(os.listdir(d)):
            src = load_mod_source(os.path.join(d, e), w.problems)
            if not src:
                continue
            old = by_id.get(src.modid)
            if old is None:
                by_id[src.modid] = src
            elif parse_version(src.version) > parse_version(old.version):
                dups.append(f"{src.modid}: using {src.version}, ignoring older {old.version}")
                by_id[src.modid] = src
            else:
                dups.append(f"{src.modid}: using {old.version}, ignoring older {src.version}")
    if dups:
        w.notes.append("Several versions of one mod were found; the newest was used: " + "; ".join(dups))

    if order:
        active = [m for m in order if m in by_id]
        missing = [m for m in order if m not in by_id and m not in VANILLA_IDS]
        idle = sorted(set(by_id) - set(order))
        if missing:
            w.problems.append("Loaded last session but no mod file found: " + ", ".join(missing))
        if idle:
            w.notes.append("Mod files present but not loaded last session (left out): " + ", ".join(idle))
        w.notes.append(f"Mod list and load order taken from the last session in the client log ({len(active)} mods).")
    else:
        active = toposort(by_id)
        w.notes.append(f"No client log used: every mod file found was included ({len(active)} mods).")

    w.sources = [van] + [by_id[m] for m in active]
    for src in w.sources:
        for k in src.assets:
            w.winner[k] = src

    # language: every en.json of every origin, later files override earlier keys
    for src in w.sources:
        for k in sorted(src.assets):
            dom, p = k.split(":", 1)
            if p == "lang/en.json":
                try:
                    entries = loads_lenient(src.read(k))
                    if isinstance(entries, dict):
                        w.lang.load(entries, dom)
                except Exception as e:
                    w.problems.append(f"{src.label}: lang file {k} failed to parse ({e})")
    return w


# ----------------------------------------------------------------------------------------------
# Guide pages (assets/<domain>/config/handbook/*.json)
# ----------------------------------------------------------------------------------------------
def collect_guides(w):
    """Port of GuiDialogSurvivalHandbook.initCustomPages: one text page per file, sorted by asset path."""
    guides, counts = [], {}
    keys = sorted(k for k in w.winner if k.split(":", 1)[1].startswith("config/handbook/") and k.endswith(".json"))
    for k in keys:
        src = w.winner[k]
        providers = [s for s in w.sources if k in s.assets]
        try:
            raw = loads_lenient(src.read(k))
        except Exception as e:
            w.problems.append(f"{src.label}: guide {k} failed to parse ({e})")
            counts.setdefault(src.modid, [0, 0])[1] += 1
            continue
        for obj in (raw if isinstance(raw, list) else [raw]):
            d = lower_keys(obj)
            code = d.get("pagecode")
            if not code:
                w.problems.append(f"{src.label}: guide {k} has no pageCode, skipped")
                counts.setdefault(src.modid, [0, 0])[1] += 1
                continue
            title_key = str(d.get("title") or "")
            text = str(d.get("text") or "")
            if len(text) < 255:
                text = w.lang.get(text)
            title = re.sub(r"<[^>]+>", "", w.lang.get(title_key)).strip() or str(code)
            g = {"code": str(code), "title": title, "vtml": text, "mod": src.modid, "asset": k,
                 "cat": str(d.get("categorycode") or "guide")}
            if title == title_key and title_key:
                w.notes.append(f"{src.label}: guide {code} has no English title entry ({title_key})")
            if len(providers) > 1:
                w.notes.append(f"{k} is shipped by {', '.join(p.modid for p in providers)}; "
                               f"the copy from {src.modid} wins")
            guides.append(g)
            counts.setdefault(src.modid, [0, 0])[0] += 1
    # the game keeps every page but links go to the last page with a given code
    seen = {}
    for g in guides:
        seen.setdefault(g["code"], []).append(g)
    for code, lst in seen.items():
        for n, g in enumerate(lst[:-1], start=2):
            g["id"] = f"{code}~{n}"
            w.notes.append(f"Page code {code} is used by more than one guide ({g['asset']})")
        lst[-1]["id"] = code
    return guides, counts


def scan_code_pages(w):
    """Mods whose DLL registers handbook pages from code cannot be read from assets: list them."""
    found = []
    for src in w.sources[1:]:
        hits = set()
        for d in src.dlls:
            try:
                b = src.read_dll(d)
            except Exception:
                continue
            if b"OnInitCustomPages" in b:
                hits.add("adds pages from code (OnInitCustomPages)")
            if b"TagConverters" in b and b"VtmlUtil" in b:
                hits.add("registers its own rich text tag")
        if hits:
            found.append((src, sorted(hits)))
    return found


def pretty_code(path):
    return path.replace("-", " ").replace("_", " ").strip().capitalize()


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(path)


# ----------------------------------------------------------------------------------------------
# Item and block pages, from asset JSON only
#
# The game builds these pages while it runs. What can be rebuilt from the files alone:
# which blocks and items have a page, their English name, the written description and
# handbook sections, grid recipes and a short "made by" line for a few other recipe kinds.
# ----------------------------------------------------------------------------------------------

_WC = {}


def wmatch(needle, hay):
    """WildcardUtil.Match: '@' + regular expression, or * wildcards; always the whole string."""
    rx = _WC.get(needle)
    if rx is None:
        try:
            if needle.startswith("@"):
                rx = re.compile("^(?:" + needle[1:] + ")$")
            elif "*" in needle:
                rx = re.compile("^" + re.escape(needle).replace(r"\*", ".*") + "$", re.I | re.S)
            else:
                rx = needle.lower()
        except re.error:
            rx = "\0never"
        _WC[needle] = rx
    if isinstance(rx, str):
        return rx == hay.lower()
    return rx.match(hay) is not None


_PH = re.compile(r"\{([^{}]*)\}")


def fill(text, parts):
    """RegistryObject.FillPlaceHolder: {name} or {a|name|b} becomes the variant value."""
    if "{" not in text or not parts:
        return text

    def rep(m):
        alts = m.group(1).split("|")
        for k, v in parts.items():
            if k in alts:
                return v
        return m.group(0)

    return _PH.sub(rep, text)


def merge(a, b):
    """Newtonsoft JContainer.Merge defaults: objects merge, arrays are joined, other values replace."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(out[k], v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return b


def solve_bytype(node, path, parts):
    """RegistryObjectType.solveByType on a small sub tree (returns a new tree)."""
    if isinstance(node, dict):
        out, picked = {}, {}
        for k, v in node.items():
            if k.lower().endswith("bytype") and len(k) > 6:
                if isinstance(v, dict):
                    for pat, val in v.items():
                        if wmatch(pat, path):
                            picked[k[:-6]] = val
                            break
            else:
                out[k] = v
        for k, v in picked.items():
            out[k] = merge(out[k], v) if isinstance(out.get(k), dict) else v
        return {k: solve_bytype(v, path, parts) for k, v in out.items()}
    if isinstance(node, list):
        return [solve_bytype(v, path, parts) for v in node]
    if isinstance(node, str):
        return fill(node, parts)
    return node


def split_code(code, default_domain):
    code = str(code)
    if ":" in code:
        d, p = code.split(":", 1)
        return d.lower(), p
    return default_domain, code


def truthy(v):
    return v is True or (isinstance(v, str) and v.lower() == "true")


def clean_name(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


# ---- JSON patches (Tavis.JsonPatch subset the game uses) ----
def ptr_parts(path):
    return [p.replace("~1", "/").replace("~0", "~") for p in str(path).split("/")[1:]]


def ptr_child(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for k in node:
            if k.lower() == key.lower():
                return node[k]
        raise KeyError(key)
    if isinstance(node, list):
        return node[int(key)]
    raise KeyError(key)


def ptr_key(node, key):
    if isinstance(node, dict) and key not in node:
        for k in node:
            if k.lower() == key.lower():
                return k
    return key


def patch_apply(doc, op, path, value, frompath):
    parts = ptr_parts(path)
    if not parts:
        raise KeyError("empty path")
    parent = doc
    for p in parts[:-1]:
        parent = ptr_child(parent, p)
    last = ptr_key(parent, parts[-1])

    def add(container, key, val):
        if isinstance(container, list):
            if key == "-":
                container.append(val)
            else:
                container.insert(int(key), val)
        elif isinstance(container, dict):
            container[key] = val
        else:
            raise KeyError(key)

    if op == "add":
        add(parent, last, value)
    elif op == "addeach":
        if not isinstance(value, list):
            raise KeyError("addeach needs a list")
        for n, v in enumerate(value):
            add(parent, last if last == "-" or isinstance(parent, dict) else str(int(last) + n), v)
    elif op == "addmerge":
        try:
            cur = ptr_child(parent, last)
        except (KeyError, IndexError, ValueError):
            add(parent, last, value)
            return
        if isinstance(cur, list):
            cur.extend(value if isinstance(value, list) else [value])
        elif isinstance(cur, dict) and isinstance(value, dict):
            new = merge(cur, value)
            cur.clear()
            cur.update(new)
        elif isinstance(parent, list):
            parent[int(last)] = value
        else:
            parent[last] = value
    elif op == "replace":
        ptr_child(parent, last)
        if isinstance(parent, list):
            parent[int(last)] = value
        else:
            parent[last] = value
    elif op == "remove":
        if isinstance(parent, list):
            del parent[int(last)]
        else:
            ptr_child(parent, last)
            del parent[last]
    elif op in ("move", "copy"):
        fparts = ptr_parts(frompath)
        fparent = doc
        for p in fparts[:-1]:
            fparent = ptr_child(fparent, p)
        flast = ptr_key(fparent, fparts[-1])
        val = copy.deepcopy(ptr_child(fparent, flast))
        if op == "move":
            if isinstance(fparent, list):
                del fparent[int(flast)]
            else:
                del fparent[flast]
        add(parent, last, val)
    else:
        raise KeyError("unknown op " + op)


PATCHED_FOLDERS = ("blocktypes/", "itemtypes/", "recipes/", "worldproperties/")
OTHER_RECIPES = (("smithing", "Smithing on an anvil"), ("knapping", "Knapping"), ("clayforming", "Clay forming"))
CHUNK = 500
MAX_COMBOS = 3000
MAX_RECIPES_PER_PAGE = 10
MAX_USED = 400


class Items:
    def __init__(self, w, stats):
        self.w = w
        self.c = {"type_files": 0, "type_failed": 0, "variants": 0, "patches": 0, "patch_ok": 0, "patch_cond": 0,
                  "patch_dep": 0, "patch_fail": 0, "patch_missing": 0, "patch_other": 0, "name_fallback": 0,
                  "grid_files": 0, "grid_read": 0, "grid_disabled": 0, "grid_bad": 0, "grid_too_many": 0,
                  "grid_expanded": 0, "grid_attached": 0, "grid_no_page": 0, "grid_unresolved": 0,
                  "other_lines": 0, "with_desc": 0, "with_recipe": 0, "with_used": 0, "with_sections": 0}
        self.cache = {}            # asset key -> parsed (and patched) JSON
        self.coll = {}             # (kind, domain, path) -> mod id            every block/item variant
        self.by_first = {}         # (kind, domain) -> {first code segment -> [paths]}
        self.pages = {}            # page code -> dict
        self.cand = []             # page candidates collected while reading types
        self.props = {}            # "domain:block/rock" -> [variant codes]
        self.match_cache = {}
        self.name_cache = {}
        self.active = {s.modid for s in w.sources} | set(VANILLA_IDS)
        self.alias_cache = {}

    # ---------------------------------------------------------------- files
    def json(self, key):
        if key not in self.cache:
            self.cache[key] = loads_lenient(self.w.winner[key].read(key))
        return self.cache[key]

    def keys(self, prefix):
        return sorted(k for k in self.w.winner if k.split(":", 1)[1].startswith(prefix) and k.endswith(".json"))

    def apply_patches(self):
        w, c = self.w, self.c
        for src in w.sources:
            for key in sorted(src.keys_under("patches/")):
                if w.winner.get(key) is not src:
                    continue
                dom = key.split(":", 1)[0]
                try:
                    patches = loads_lenient(src.read(key))
                except Exception as e:
                    w.problems.append(f"{src.label}: patch file {key} is cut off or malformed ({e})")
                    continue
                if isinstance(patches, dict):
                    patches = [patches]
                for p in patches if isinstance(patches, list) else []:
                    p = lower_keys(p)
                    target = p.get("file")
                    if not target:
                        continue
                    tdom, tpath = split_code(target, dom)
                    tpath = tpath.lower()
                    if not tpath.startswith(PATCHED_FOLDERS):
                        c["patch_other"] += 1
                        continue
                    c["patches"] += 1
                    if p.get("enabled") is False:
                        c["patch_cond"] += 1
                        continue
                    if p.get("condition"):
                        c["patch_cond"] += 1          # depends on the server's world settings: unknown here
                        continue
                    deps = p.get("dependson")
                    if isinstance(deps, list):
                        ok = True
                        for d in deps:
                            d = lower_keys(d)
                            ok = ok and ((str(d.get("modid", "")).lower() in self.active) ^ truthy(d.get("invert")))
                        if not ok:
                            c["patch_dep"] += 1
                            continue
                    if tpath.endswith("*"):
                        pre = tpath.rstrip("*")
                        targets = [k for k in w.winner if k.startswith(f"{tdom}:{pre}") and k.endswith(".json")]
                    else:
                        targets = [f"{tdom}:{tpath if tpath.endswith('.json') else tpath + '.json'}"]
                    for t in targets:
                        if t not in w.winner:
                            c["patch_missing"] += 1
                            continue
                        try:
                            doc = self.json(t)
                            patch_apply(doc, str(p.get("op", "")).lower(), p.get("path", ""),
                                        copy.deepcopy(p.get("value")), p.get("frompath"))
                            c["patch_ok"] += 1
                        except Exception:
                            c["patch_fail"] += 1

    # ---------------------------------------------------------------- types and variants
    def load_props(self):
        for key in self.keys("worldproperties/"):
            try:
                d = lower_keys(self.json(key))
            except Exception:
                continue
            if not d:
                continue
            dom, p = key.split(":", 1)
            loc = p[len("worldproperties/"):-len(".json")]
            codes = []
            for v in d.get("variants") or []:
                code = lower_keys(v).get("code")
                if code is not None:
                    codes.append(split_code(code, dom)[1])
            self.props[f"{dom}:{loc}"] = (str(d.get("code") or loc), codes)

    def prop(self, ref, dom):
        d, p = split_code(ref, dom)
        return self.props.get(f"{d}:{p.lower()}") or self.props.get(f"game:{p.lower()}")

    def variants(self, groups, dom):
        """Port of ModRegistryObjectTypeLoader.GatherVariants. Returns a list of {group: value}."""
        final, mul = [], {}
        groups = [lower_keys(g) for g in groups if isinstance(g, dict)]
        for g in groups:
            combine = str(g.get("combine") or "multiply").lower()
            code = g.get("code")
            props = []
            if g.get("loadfromproperties"):
                props.append(g["loadfromproperties"])
            if isinstance(g.get("loadfrompropertiescombine"), list):
                props += g["loadfrompropertiescombine"]
            for ref in props:
                wp = self.prop(ref, dom)
                if not wp:
                    break
                name = str(code) if code is not None else wp[0].split("/")[-1]
                if combine == "add":
                    final += [{name: v} for v in wp[1]]
                elif combine == "multiply":
                    mul.setdefault(name, []).extend((v, None) for v in wp[1])
            states = g.get("states")
            if isinstance(states, list) and code is not None:
                states = [str(s) for s in states]
                if combine == "add":
                    final += [{str(code): s} for s in states]
                elif combine == "multiply":
                    entries = [(s, None) for s in states]
                    for g2 in groups:
                        if str(g2.get("combine") or "").lower() != "selectivemultiply" or g2.get("onvariant") != code:
                            continue
                        new = []
                        for ent in entries:
                            if ent[0] == g2.get("code") and isinstance(g2.get("states"), list):
                                base_parts = ent[1] or [(str(code), ent[0])]
                                for s2 in g2["states"]:
                                    s2 = str(s2)
                                    new.append((ent[0] if not s2 else f"{ent[0]}-{s2}",
                                                base_parts + [(str(g2["code"]), s2)]))
                            else:
                                new.append(ent)
                        entries = new
                    mul.setdefault(str(code), []).extend(entries)
        names = list(mul)
        total = 1
        for n in names:
            total *= len(mul[n])
        for j in range(total):
            idx, parts = j, {}
            for n in names:
                arr = mul[n]
                val, multi = arr[idx % len(arr)]
                idx //= len(arr)
                if multi:
                    for t, cde in multi:
                        parts[t] = cde
                else:
                    parts[n] = val
            final.append(parts)
        return final

    def load_types(self, kind, folder):
        w, c = self.w, self.c
        for key in self.keys(folder):
            dom = key.split(":", 1)[0]
            src = w.winner[key]
            c["type_files"] += 1
            try:
                obj = self.json(key)
                if obj is None:
                    continue
                if not isinstance(obj, dict):
                    raise ValueError("not an object")
                low = {k.lower(): k for k in obj}
                if obj.get(low.get("enabled", "\0")) is False:
                    continue
                code = obj.get(low.get("code", "\0"))
                if not code:
                    raise ValueError("no code")
                tdom, base = split_code(code, dom)
                groups = obj.get(low.get("variantgroups", "\0"))
                vlist = self.variants(groups, tdom) if isinstance(groups, list) and groups else [{}]
                if not vlist:
                    vlist = [{}]
                skip = [split_code(x, tdom) for x in obj.get(low.get("skipvariants", "\0")) or []]
                allow = obj.get(low.get("allowedvariants", "\0"))
                allow = [split_code(x, tdom) for x in allow] if isinstance(allow, list) else None
                has_bt = {n: (n + "bytype") in low for n in ("creativeinventory", "creativeinventorystacks", "attributes")}
                static = {}
                for parts in vlist:
                    path = base + "".join("-" + v for v in parts.values() if v)
                    if any(d == tdom and wmatch(p, path) for d, p in skip):
                        continue
                    if allow is not None and not any(d == tdom and wmatch(p, path) for d, p in allow):
                        continue
                    c["variants"] += 1
                    ck = (kind, tdom, path)
                    self.coll[ck] = src.modid
                    self.by_first.setdefault((kind, tdom), {}).setdefault(path.split("-", 1)[0], []).append(path)
                    self.consider(ck, obj, low, parts, has_bt, static, src.modid)
            except Exception as e:
                c["type_failed"] += 1
                w.problems.append(f"{src.label}: {key} could not be read as a {kind} type, file is cut off or malformed ({e})")

    @staticmethod
    def top(obj, low, name, path):
        base = obj.get(low[name]) if name in low else None
        bt = obj.get(low[name + "bytype"]) if (name + "bytype") in low else None
        if isinstance(bt, dict):
            for pat, val in bt.items():
                if wmatch(pat, path):
                    return merge(base, val) if isinstance(base, dict) and isinstance(val, dict) else val
        return base

    def consider(self, ck, obj, low, parts, has_bt, static, modid):
        """CollectibleObject.GetHandBookStacks: does this variant get a handbook page?"""
        kind, dom, path = ck
        attrs = self.top(obj, low, "attributes", path)
        hb = None
        if isinstance(attrs, dict):
            alow = {k.lower(): k for k in attrs}
            if "handbook" in alow or "handbookbytype" in alow:
                hb = self.top(attrs, alow, "handbook", path)
                hb = solve_bytype(hb, path, parts) if isinstance(hb, dict) else None
        hb = hb or {}
        if truthy(hb.get("exclude")):
            return
        ci = self.top(obj, low, "creativeinventory", path)
        in_tab = False
        if isinstance(ci, dict):
            for pats in ci.values():
                for p in pats if isinstance(pats, list) else []:
                    if p == "*" or wmatch(fill(str(p), parts), path):
                        in_tab = True
                        break
                if in_tab:
                    break
        cis = self.top(obj, low, "creativeinventorystacks", path)
        stacks = []
        if isinstance(cis, list):
            for grp in cis:
                for st in (lower_keys(grp).get("stacks") or []) if isinstance(grp, dict) else []:
                    st = lower_keys(st)
                    if st.get("code"):
                        sk = "item" if str(st.get("type", "block")).lower() == "item" else "block"
                        sd, sp = split_code(fill(str(st["code"]), parts), dom)
                        stacks.append((sk, sd, sp))
        if not in_tab and not stacks and not truthy(hb.get("include")):
            return
        targets = [ck]
        if stacks and not truthy(hb.get("ignoreCreativeInvStacks")):
            targets = list(dict.fromkeys(stacks))
        self.cand.append((targets, ck, hb, modid))

    # ---------------------------------------------------------------- names and pages
    @staticmethod
    def page_code(kind, dom, path):
        return f"{kind}-{path}" if dom == "game" else f"{kind}-{dom}:{path}"

    def name(self, ck):
        n = self.name_cache.get(ck)
        if n is None:
            kind, dom, path = ck
            v = self.w.lang.matching(f"{dom}:{kind}-{path}")
            n = clean_name(v) if v else ""
            if not n or "{" in n:
                if ck in self.coll and not v:
                    self.c["name_fallback"] += 1
                n = pretty_code(path) if not n else re.sub(r"\s*\{[^}]*\}\s*", " ", n).strip() or pretty_code(path)
            self.name_cache[ck] = n
        return n

    def make_pages(self):
        lang = self.w.lang
        for targets, owner, hb, modid in self.cand:
            for ck in targets:
                if ck not in self.coll:
                    ck = owner
                code = self.page_code(*ck)
                if code in self.pages:
                    continue
                kind, dom, path = ck
                page = {"ck": ck, "mod": self.coll.get(ck, modid), "name": self.name(ck), "desc": None,
                        "sections": [], "recipes": [], "seen": set(), "used": {}, "other": [], "more": 0}
                d = lang.matching(f"{dom}:{kind}desc-{path}")
                if d and d.strip():
                    page["desc"] = d
                for sec in hb.get("extraSections") or []:
                    s = lower_keys(sec)
                    title = lang.get(str(s.get("title") or ""))
                    if isinstance(s.get("textparts"), list):
                        text = ", ".join(str(x) for x in s["textparts"])
                    else:
                        text = lang.get(str(s.get("text") or ""))
                    if text:
                        page["sections"].append((clean_name(title), text))
                short = path if dom == "game" else f"{dom}:{path}"
                for kshort in dict.fromkeys((short, path)):
                    ht = lang.matching(f"{dom}:{kind}-handbooktext-{kshort}")
                    if ht:
                        tt = lang.matching(f"{dom}:{kind}-handbooktitle-{kshort}")
                        page["sections"].append((clean_name(tt) if tt else "", ht))
                        break
                self.pages[code] = page
        self.cand = None

    # ---------------------------------------------------------------- matching collectibles
    def matches(self, kind, dom, pattern, allowed=None, skip=None):
        """Collectible paths matching a recipe code with * wildcards (CollectiblePreSearch + WildcardUtil.Match)."""
        key = (kind, dom, pattern, tuple(allowed) if allowed else None, tuple(skip) if skip else None)
        hit = self.match_cache.get(key)
        if hit is not None:
            return hit
        out = []
        doms = [d for (k, d) in self.by_first if k == kind] if "*" in dom else [dom]
        try:
            rx = re.compile("^" + re.escape(pattern).replace(r"\*", "(.*)") + "$")
        except re.error:
            rx = None
        first = pattern.split("-", 1)[0]
        for d in doms:
            idx = self.by_first.get((kind, d), {})
            pools = [idx.get(first, [])] if "*" not in first else idx.values()
            for pool in pools:
                for p in pool:
                    m = rx.match(p) if rx else None
                    if not m:
                        continue
                    g = m.group(1) if m.groups() else ""
                    if allowed and g not in allowed:
                        continue
                    if skip and g in skip:
                        continue
                    out.append((d, p, g))
        self.match_cache[key] = out
        return out

    # ---------------------------------------------------------------- grid recipes
    def ingredient_view(self, d, dom, combo):
        """One ingredient as [label, page code, quantity, note] plus the collectibles it stands for; None = unresolvable."""
        kind = "item" if str(d.get("type", "block")).lower() == "item" else "block"
        qty = d.get("quantity", d.get("stacksize", 1))
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 1
        note = "tool, not used up" if truthy(d.get("istool")) else ""
        code = d.get("code")
        if code is None or code == "*:*":
            return [self.tag_label(d.get("tags"), kind), "", qty, note], []
        cdom, cpath = split_code(code, dom)
        name = d.get("name")
        if name and "*" in cpath and "{" not in cpath and name in combo:
            cpath = cpath.replace("*", combo[name])
        cpath = fill(cpath, combo)
        if cpath.startswith("@"):
            return ["Any matching " + kind, "", qty, note], []
        if "{" in cpath:
            cpath = _PH.sub("*", cpath)     # a variable that does not change the output: any kind fits
        if "*" in cpath or "*" in cdom:
            ms = self.matches(kind, cdom, cpath, d.get("allowedvariants"), d.get("skipvariants"))
            if not ms:
                return None
            cks = [(kind, m[0], m[1]) for m in ms]
            show = next((ck for ck in cks if self.page_code(*ck) in self.pages), cks[0])
            label = self.name(show)
            if len(cks) > 1:
                label = f"{label} (any of {len(cks)} kinds)"
            pc = self.page_code(*show)
            return [label, pc if pc in self.pages else "", qty, note], cks
        ck = (kind, cdom, cpath)
        if ck not in self.coll:
            return None
        pc = self.page_code(*ck)
        return [self.name(ck), pc if pc in self.pages else "", qty, note], [ck]

    @staticmethod
    def tag_label(tags, kind):
        words = []

        def walk(t):
            if isinstance(t, str):
                words.append(t)
            elif isinstance(t, list):
                for x in t:
                    walk(x)
            elif isinstance(t, dict):
                for x in t.values():
                    walk(x)

        walk(tags)
        words = [x.replace("-", " ") for x in dict.fromkeys(words)][:4]
        return ("Any " + kind + " tagged: " + ", ".join(words)) if words else "Any " + kind

    def name_mapping(self, defs, dom, rec):
        """RecipeBase.GetNameToCodeMapping: variable name -> set of values."""
        mapping = {}
        rec_allow = {k: set(map(str, v)) for k, v in (rec.get("allowedvariants") or {}).items()} \
            if isinstance(rec.get("allowedvariants"), dict) else {}
        rec_skip = {k: set(map(str, v)) for k, v in (rec.get("skipvariants") or {}).items()} \
            if isinstance(rec.get("skipvariants"), dict) else {}
        for d in defs:
            code = d.get("code")
            if code is None:
                continue
            kind = "item" if str(d.get("type", "block")).lower() == "item" else "block"
            cdom, cpath = split_code(code, dom)
            if cpath.startswith("@"):
                continue
            if "{" in cpath:
                names = _PH.findall(cpath)
                pat = re.sub(r"\\\{[^{}]*\\\}", "(.+)", re.escape(cpath)).replace(r"\*", ".*")
                try:
                    rx = re.compile("^" + pat + "$")
                except re.error:
                    continue
                first = cpath.split("-", 1)[0]
                idx = self.by_first.get((kind, cdom), {})
                pools = [idx.get(first, [])] if "{" not in first and "*" not in first else idx.values()
                for pool in pools:
                    for p in pool:
                        m = rx.match(p)
                        if not m:
                            continue
                        vals = m.groups()
                        if any((n in rec_allow and v not in rec_allow[n]) or (n in rec_skip and v in rec_skip[n])
                               for n, v in zip(names, vals)):
                            continue
                        for n, v in zip(names, vals):
                            mapping.setdefault(n, set()).add(v)
                for n in names:
                    mapping.setdefault(n, set())
            elif "*" in cpath and d.get("name"):
                ms = self.matches(kind, cdom, cpath, d.get("allowedvariants"), d.get("skipvariants"))
                mapping[str(d["name"])] = {m[2] for m in ms}
        return mapping

    @staticmethod
    def output_vars(mapping, opath):
        """Keep the variables the output code uses. The others give the same result, so they are shown as 'any kind'."""
        used = set()
        for m in _PH.findall(opath):
            used.update(m.split("|"))
        return {n: v for n, v in mapping.items() if n in used}

    def load_grid(self):
        c = self.c
        for key in self.keys("recipes/grid/"):
            dom = key.split(":", 1)[0]
            c["grid_files"] += 1
            try:
                data = self.json(key)
            except Exception as e:
                self.w.problems.append(f"{self.w.winner[key].label}: recipe file {key} is cut off or malformed ({e})")
                continue
            for rec in data if isinstance(data, list) else [data]:
                if isinstance(rec, dict):
                    c["grid_read"] += 1
                    try:
                        self.grid_recipe(lower_keys(rec), dom)
                    except Exception:
                        c["grid_bad"] += 1

    def grid_recipe(self, r, dom):
        c = self.c
        if r.get("enabled") is False:
            c["grid_disabled"] += 1
            return
        out = lower_keys(r.get("output"))
        pattern = r.get("ingredientpattern")
        ings = r.get("ingredients")
        if not out.get("code") or not isinstance(pattern, str) or not isinstance(ings, dict):
            c["grid_bad"] += 1
            return
        pattern = re.sub(r"[,\t\r\n]", "", pattern)
        try:
            width, height = int(r.get("width", 3)), int(r.get("height", 3))
        except (TypeError, ValueError):
            c["grid_bad"] += 1
            return
        if width * height != len(pattern):
            c["grid_bad"] += 1
            return
        defs = {k: lower_keys(v) for k, v in ings.items() if isinstance(v, dict)}
        letters = [ch for ch in dict.fromkeys(pattern) if ch not in " _"]
        if any(ch not in defs for ch in letters):
            c["grid_bad"] += 1
            return
        okind = "item" if str(out.get("type", "block")).lower() == "item" else "block"
        odom, opath = split_code(out["code"], dom)
        mapping = self.name_mapping(list(defs.values()), dom, r)
        if any(not v for v in mapping.values()):
            c["grid_unresolved"] += 1
            return
        mapping = self.output_vars(mapping, opath)
        names = list(mapping)
        total = 1
        for n in names:
            total *= len(mapping[n])
        if total > MAX_COMBOS:
            c["grid_too_many"] += 1
            return
        oqty = out.get("quantity", out.get("stacksize", 1))
        shapeless = truthy(r.get("shapeless"))
        trait = r.get("requirestrait")
        note = ""
        if trait:
            tn = self.w.lang.get_if_exists(f"traitname-{trait}") or self.w.lang.get_if_exists(f"game:traitname-{trait}")
            note = "Needs the trait: " + (clean_name(tn) if tn else str(trait))
        any_ok = False
        for values in itertools.product(*[sorted(mapping[n]) for n in names]):
            combo = dict(zip(names, values))
            ock = (okind, odom, fill(opath, combo))
            if ock not in self.coll:
                continue
            views, fail = {}, False
            for ch in letters:
                v = self.ingredient_view(defs[ch], dom, combo)
                if v is None:
                    fail = True
                    break
                views[ch] = v
            if fail:
                continue
            any_ok = True
            c["grid_expanded"] += 1
            ocode = self.page_code(*ock)
            for ch in letters:
                for ck in views[ch][1][:MAX_USED]:
                    pg = self.pages.get(self.page_code(*ck))
                    if pg is not None and ocode in self.pages and len(pg["used"]) < MAX_USED:
                        pg["used"][ocode] = 1
            page = self.pages.get(ocode)
            if page is None:
                c["grid_no_page"] += 1
                continue
            order = {ch: i for i, ch in enumerate(letters)}
            if shapeless:
                counts = {}
                for ch in pattern:
                    if ch in order:
                        counts[ch] = counts.get(ch, 0) + 1
                il = []
                for ch in letters:
                    v = list(views[ch][0])
                    v[2] = v[2] * counts.get(ch, 1)
                    il.append(v)
                rec = {"s": 1, "i": il, "q": oqty}
            else:
                rec = {"w": width, "h": height, "c": [order.get(ch, -1) for ch in pattern],
                       "i": [views[ch][0] for ch in letters], "q": oqty}
            if note:
                rec["n"] = note
            sig = json.dumps(rec, sort_keys=True, ensure_ascii=False)
            if sig in page["seen"]:
                continue
            page["seen"].add(sig)
            if len(page["recipes"]) < MAX_RECIPES_PER_PAGE:
                page["recipes"].append(rec)
                c["grid_attached"] += 1
            else:
                page["more"] += 1
        if not any_ok:
            c["grid_unresolved"] += 1

    # ---------------------------------------------------------------- other recipe kinds, one line each
    def load_other(self):
        for folder, label in OTHER_RECIPES:
            for key in self.keys(f"recipes/{folder}/"):
                dom = key.split(":", 1)[0]
                try:
                    data = self.json(key)
                except Exception:
                    continue
                for rec in data if isinstance(data, list) else [data]:
                    try:
                        r = lower_keys(rec)
                        if r.get("enabled") is False:
                            continue
                        ing, out = lower_keys(r.get("ingredient")), lower_keys(r.get("output"))
                        if not ing.get("code") or not out.get("code"):
                            continue
                        self.other_lines(label, [ing], out, dom, "")
                    except Exception:
                        pass
        for key in self.keys("recipes/barrel/"):
            dom = key.split(":", 1)[0]
            try:
                data = self.json(key)
            except Exception:
                continue
            for rec in data if isinstance(data, list) else [data]:
                try:
                    r = lower_keys(rec)
                    if r.get("enabled") is False:
                        continue
                    ings = [lower_keys(i) for i in r.get("ingredients") or [] if isinstance(i, dict)]
                    out = lower_keys(r.get("output"))
                    if not ings or not out.get("code"):
                        continue
                    hours = r.get("sealhours")
                    tail = f", sealed for {hours:g} hours" if isinstance(hours, (int, float)) and hours else ""
                    self.other_lines("Barrel", ings, out, dom, tail)
                except Exception:
                    pass

    def other_lines(self, label, ings, out, dom, tail):
        okind = "item" if str(out.get("type", "block")).lower() == "item" else "block"
        odom, opath = split_code(out["code"], dom)
        mapping = self.name_mapping(ings, dom, {})
        if any(not v for v in mapping.values()):
            return
        mapping = self.output_vars(mapping, opath)
        names = list(mapping)
        total = 1
        for n in names:
            total *= len(mapping[n])
        if total > MAX_COMBOS:
            return
        for values in itertools.product(*[sorted(mapping[n]) for n in names]):
            combo = dict(zip(names, values))
            page = self.pages.get(self.page_code(okind, odom, fill(opath, combo)))
            if page is None or len(page["other"]) >= 6:
                continue
            parts = []
            for ing in ings:
                v = self.ingredient_view(ing, dom, combo)
                if v is None:
                    parts = None
                    break
                lit = ing.get("litres")
                amount = f"{lit:g} L " if isinstance(lit, (int, float)) else (f"{v[0][2]}× " if v[0][2] > 1 else "")
                parts.append(amount + v[0][0])
            if not parts:
                continue
            line = (f"{label}: " + " + ".join(parts) if label == "Barrel" else f"{label} from {parts[0]}") + tail
            if line not in page["other"]:
                page["other"].append(line)
                self.c["other_lines"] += 1

    # ---------------------------------------------------------------- lookups used by the guide renderer
    def lookup(self, kind, dom, path):
        ck = (kind, dom, path)
        if "*" in path:
            ms = self.matches(kind, dom, path)
            if not ms:
                return None
            ck = (kind, ms[0][0], ms[0][1])
        elif ck not in self.coll:
            return None
        code = self.page_code(*ck)
        return (code if code in self.pages else self.resolve_alias(code), self.name(ck))

    def resolve_alias(self, code):
        """A link to a variant without its own page goes to the nearest page of the same block or item."""
        if code in self.alias_cache:
            return self.alias_cache[code]
        hit = None
        base = re.sub(r"-\{.*\}$", "", code)
        if base in self.pages:
            hit = base
        else:
            probe = base
            while hit is None and "-" in probe:
                pre = probe + "-"
                cands = [p for p in self.sorted_codes_from(pre)]
                if cands:
                    hit = cands[0]
                    break
                probe = probe.rsplit("-", 1)[0]
                if probe in self.pages:
                    hit = probe
                if probe.count("-") < 1:
                    break
        self.alias_cache[code] = hit
        return hit

    def sorted_codes_from(self, prefix):
        if not hasattr(self, "_sorted"):
            self._sorted = sorted(self.pages)
        i = bisect.bisect_left(self._sorted, prefix)
        out = []
        while i < len(self._sorted) and self._sorted[i].startswith(prefix) and len(out) < 1:
            out.append(self._sorted[i])
            i += 1
        return out

    # ---------------------------------------------------------------- output
    def write(self, out, renderer, mods_out, mod_index):
        c = self.c
        folder = os.path.join(out, "data", "items")
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        os.makedirs(folder, exist_ok=True)
        per_mod = {}
        for code, p in self.pages.items():
            per_mod.setdefault(p["mod"], []).append((p["name"].casefold(), code))
        index, total, size = [], 0, 0
        for src in self.w.sources:
            lst = sorted(per_mod.get(src.modid, []))
            if not lst:
                continue
            mi = mod_index[src.modid]
            mods_out[mi]["n"] = len(lst)
            total += len(lst)
            index.append([mi, [[code, self.pages[code]["name"]] for _n, code in lst]])
            safe = re.sub(r"[^a-z0-9_-]", "_", src.modid)
            mods_out[mi]["id"] = src.modid
            for k in range(0, len(lst), CHUNK):
                chunk = []
                for _n, code in lst[k:k + CHUNK]:
                    p = self.pages[code]
                    e = {}
                    try:
                        if p["desc"]:
                            e["d"] = renderer.render(p["desc"])
                            c["with_desc"] += 1
                        secs = [[t, renderer.render(x)] for t, x in p["sections"]]
                        if secs:
                            e["x"] = secs
                            c["with_sections"] += 1
                    except Exception as ex:
                        self.w.problems.append(f"item page {code}: text failed to convert ({ex})")
                    if p["recipes"]:
                        e["r"] = p["recipes"]
                        if p["more"]:
                            e["r"][-1] = dict(e["r"][-1], n=(e["r"][-1].get("n", "") + f" (+{p['more']} more ways in the game)").strip())
                        c["with_recipe"] += 1
                    if p["other"]:
                        e["o"] = p["other"]
                    if p["used"]:
                        e["u"] = sorted(p["used"], key=lambda x: self.pages[x]["name"].casefold())
                        c["with_used"] += 1
                    chunk.append(e or 0)
                size += write_json(os.path.join(folder, f"{safe}-{k // CHUNK}.json"), chunk)
        size_idx = write_json(os.path.join(out, "data", "items.json"), {"chunk": CHUNK, "mods": index})
        c["index_kb"], c["chunks_kb"], c["pages"] = size_idx // 1024, size // 1024, total
        return total

    def report_lines(self):
        c = self.c
        return [
            "Item and block pages", "--------------------",
            f"Block and item type files read: {c['type_files']} (failed: {c['type_failed']}); "
            f"variants: {c['variants']}; pages: {c.get('pages', 0)}",
            f"Pages with a written description: {c['with_desc']}; with handbook sections: {c['with_sections']}; "
            f"with a grid recipe: {c['with_recipe']}; with a 'used to craft' list: {c['with_used']}",
            f"Names not found in the language files (shown from the code instead): {c['name_fallback']}",
            f"items.json: {c.get('index_kb', 0)} KB; item detail files: {c.get('chunks_kb', 0)} KB",
            f"JSON patches aimed at types and recipes: {c['patches']}; applied: {c['patch_ok']}; "
            f"skipped, other mod not present: {c['patch_dep']}; skipped, depends on world settings or disabled: "
            f"{c['patch_cond']}; target file not found: {c['patch_missing']}; failed: {c['patch_fail']}",
            f"Grid recipes read: {c['grid_read']} in {c['grid_files']} files; disabled: {c['grid_disabled']}; "
            f"malformed: {c['grid_bad']}; ingredients or output not found: {c['grid_unresolved']}; "
            f"too many variants to expand: {c['grid_too_many']}",
            f"Recipe variants worked out: {c['grid_expanded']}; shown on a page: {c['grid_attached']}; "
            f"output has no handbook page: {c['grid_no_page']}",
            f"'Also made by' lines (smithing, knapping, clay forming, barrel): {c['other_lines']}",
            "Not included, because the game computes it while running: item stats and tooltips, drops, "
            "smelting, cooking and meal recipes, alloys, trader lists, creature drops, storage notes, "
            "and names or recipes that depend on item attributes or on the server's world settings.",
        ]


def build_items(w, stats):
    it = Items(w, stats)
    it.apply_patches()
    it.load_props()
    it.load_types("block", "blocktypes/")
    it.load_types("item", "itemtypes/")
    it.make_pages()
    it.load_grid()
    it.load_other()
    return it


def main():
    ap = argparse.ArgumentParser(description="Build the handbook web site data from the game and mod assets.")
    ap.add_argument("--game", help="game folder (contains assets\\survival). Default: two folders above this script")
    ap.add_argument("--mods", action="append", help="mod folder; repeat for several. Default: the folders the last "
                                                    "game session searched, read from the client log")
    ap.add_argument("--client-log", help="client-main.log to read the mod folders and load order from")
    ap.add_argument("--no-log", action="store_true", help="ignore the client log; include every mod file found")
    ap.add_argument("--out", help="site folder. Default: ..\\docs next to this script")
    ap.add_argument("--no-items", action="store_true", help="guides only, skip the item and block pages")
    args = ap.parse_args()

    out = args.out or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs"))
    w = discover(args)
    guides, guide_counts = collect_guides(w)
    stats = {}

    items = None
    if not args.no_items and "build_items" in globals():
        items = build_items(w, stats)

    guide_ids = {g["id"] for g in guides}
    guide_by_code = {g["code"]: g["id"] for g in guides}

    def resolve(code):
        if code in guide_by_code:
            return guide_by_code[code]
        if items and code in items.pages:
            return code
        if items:
            return items.resolve_alias(code)
        return None

    def stack_name(kind, code):
        dom, _, path = code.rpartition(":")
        dom = dom or "game"
        short = path if dom == "game" else f"{dom}:{path}"
        page = f"{kind}-{short}"
        if items:
            hit = items.lookup(kind, dom, path)
            if hit:
                return hit
        name = w.lang.matching(f"{dom}:{kind}-{path}")
        return (None, re.sub(r"<[^>]+>", "", name) if name else pretty_code(path.replace("*", "")))

    r = Renderer(resolve, stack_name, stats)
    mods_out, mod_index = [], {}
    for src in w.sources:
        mod_index[src.modid] = len(mods_out)
        mods_out.append({"id": src.modid, "name": src.name, "v": src.version, "g": [], "n": 0})
    guides_out = []
    for g in guides:
        try:
            body = r.render(g["vtml"])
        except Exception as e:
            w.problems.append(f"guide {g['code']} ({g['mod']}): text failed to convert ({e})")
            guide_counts[g["mod"]][0] -= 1
            guide_counts[g["mod"]][1] += 1
            continue
        mods_out[mod_index[g["mod"]]]["g"].append(len(guides_out))
        guides_out.append({"id": g["id"], "t": g["title"], "m": mod_index[g["mod"]], "h": body})

    item_total = 0
    if items:
        item_total = items.write(out, r, mods_out, mod_index)

    data = {
        "meta": {"generated": datetime.date.today().isoformat(), "game": w.game_version,
                 "mods": len(w.sources) - 1, "guides": len(guides_out), "items": item_total},
        "mods": mods_out,
        "guides": guides_out,
    }
    size = write_json(os.path.join(out, "data", "handbook.json"), data)

    # ---- report (no folder paths: this file is part of the site repo) ----
    code_pages = scan_code_pages(w)
    lines = ["Handbook site build report", "==========================", "",
             f"Generated: {data['meta']['generated']}", f"Game version: {w.game_version}",
             f"Mods read: {len(w.sources) - 1}", f"Guide pages: {len(guides_out)}",
             f"Item and block pages: {item_total}", f"handbook.json: {size // 1024} KB", "",
             "Guide pages per mod", "-------------------"]
    for src in w.sources:
        ok, bad = guide_counts.get(src.modid, [0, 0])
        if ok or bad:
            lines.append(f"{ok:4d}  {src.name} {src.version} [{src.modid}]" + (f"   FAILED: {bad}" if bad else ""))
    lines += ["", "Pages that could not be read from asset files", "---------------------------------------------"]
    if code_pages:
        for src, hits in code_pages:
            lines.append(f"{src.name} [{src.modid}]: {'; '.join(hits)}")
    else:
        lines.append("none found")
    lines.append("The Tutorials tab (interactive tutorials) and the separate command handbook are built by game "
                 "code and are not part of this site.")
    lines += ["", "Problems", "--------"] + (w.problems or ["none"])
    if items:
        lines += [""] + items.report_lines()
    lines += ["", "Markup", "------",
              "Tags seen: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.get("tags", {}).items())),
              "Tags the handbook does not know (words kept, tag dropped): " +
              (", ".join(f"{k} {v}" for k, v in sorted(stats.get("unknown_tags", {}).items())) or "none"),
              "Hotkey codes shown by their code name (no default key known): " +
              (", ".join(sorted(stats.get("hotkeys_unmapped", {}))) or "none"),
              "Links to other protocols shown as plain text: " +
              (", ".join(f"{k} {v}" for k, v in sorted(stats.get("other_links", {}).items())) or "none")]
    dead = stats.get("dead_links", {})
    lines += [f"Handbook links whose target page is not in this copy: {len(dead)} targets, "
              f"{sum(dead.values())} links"]
    lines += ["  " + ", ".join(sorted(dead)[:400])] if dead else []
    lines += ["", "Notes", "-----"] + (w.notes or ["none"])
    with open(os.path.join(out, "data", "build-report.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"Wrote {len(guides_out)} guide pages and {item_total} item pages from {len(w.sources) - 1} mods "
          f"+ the game to {os.path.join(out, 'data')}")
    print(f"Problems: {len(w.problems)} (see data\\build-report.txt)")


if __name__ == "__main__":
    main()

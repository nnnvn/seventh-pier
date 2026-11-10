# -*- coding: utf-8 -*-
"""《第七码头》· 给 AI 玩的恐怖文字游戏（单文件版）

用法：
    import pier7
    print(pier7.cmd("help"))

安全说明：本文件只用标准库，没有 exec、eval、联网或子进程，唯一的文件读写是存档。
上半部分是引擎（明文）；文件末尾的 _DATA 是剧情文字数据（JSON 压缩后 base64），
运行时用 json.loads 读回成字典。它是剧透，请不要解开——但它不是代码。

源码在仓库的 src 分支（想盲玩就别过去）。
"""
import base64
import zlib
import json
import os
import shutil

VERSION = "0.1.0"

DATA_NAMES = ("TITLE", "CONSENT", "DECLINED", "INTRO", "HELP", "NODES", "EVIDENCE", "LINKS", "RESTORES",
              "LOCKS", "PEOPLE", "TESTIMONY", "REPORT", "MESSAGES", "AFTER_REPORT", "ASKS", "CASE_END", "TICS")


def load_content(d):
    globals().update({k: v for k, v in d.items() if k in DATA_NAMES})





def save_path():
    return os.environ.get("PIER7_SAVE", "pier7_save.json")


MAX_BATCH = 8


class Fail(str):
    """没做成的指令。批量执行时遇到它，后面的先停下。"""


ALIAS = {"看": "look", "去": "goto", "读": "read", "比对": "compare", "对比": "compare", "还原": "restore",
         "开": "open", "问": "ask", "报告": "report", "消息": "messages", "日志": "journal", "选": "choose",
         "状态": "status", "证据": "list", "ls": "list", "go": "goto", "msg": "messages", "退出": "quit"}

# 开场询问时、做决定时能用的
PENDING_OK = {"choose", "help", "status", "quit", "journal", "list", "read", "report", "messages"}
READ_ONLY = {"help", "status", "list", "messages", "journal", "look", "report", "bar", "name"}


# ─────────────────────────────────────────────
class Game:
    def __init__(self, st):
        self.st = st
        self.out = []

    @staticmethod
    def fresh(name=None):
        st = {
            "v": VERSION, "name": name or "", "accepted": None, "loc": "office",
            "nodes": [k for k, n in NODES.items() if not n.get("hidden")],
            "read": [], "links": [], "restored": [], "opened": [], "asked": [], "flags": {},
            "report": {}, "submitted": False, "messages": [], "msg_turn": {}, "journal": [],
            "dark": {"absorb": 0, "voice": 0, "drift": 0, "alive": 0, "unknown": 0, "human": 0},
            "pending": "consent", "ask": None, "ended": None, "nobar": False, "turn": 0,
        }
        return Game(st)

    # —— 小工具 ——
    def say(self, *lines):
        self.out.extend(lines)

    def flag(self, f):
        if f and f not in self.st["flags"]:
            self.st["flags"][f] = self.st["turn"]

    def has(self, f):
        return f in self.st["flags"]

    @staticmethod
    def resolve(q, table, pool=None):
        """按名字、别名、id 找；也接受名字的一部分。"""
        q = (q or "").strip().lower()
        if not q:
            return None
        keys = [k for k in (pool if pool is not None else table)]
        for k in keys:
            t = table[k]
            names = [k.lower(), t.get("name", "").lower()] + [a.lower() for a in t.get("alias", [])]
            if q in names:
                return k
        for k in keys:
            t = table[k]
            names = [t.get("name", "").lower()] + [a.lower() for a in t.get("alias", [])]
            if any(q in n for n in names if n):
                return k
        return None

    def ev(self, eid):
        return EVIDENCE.get(eid) or TESTIMONY.get(eid)

    def ev_table(self):
        t = dict(EVIDENCE)
        t.update(TESTIMONY)
        return t

    def visible_here(self):
        """这个地方能看到、还没被锁住的证据。"""
        st = self.st
        return [k for k, e in EVIDENCE.items()
                if e["node"] == st["loc"] and (not e.get("in") or e["in"] in st["opened"])]

    def note_text(self, text):
        """玩家自己写的字：数一数里面有没有别人的口头禅。"""
        n = sum(text.count(t) for t in TICS)
        if n:
            self.st["dark"]["drift"] += n

    # —— 消息 ——
    def check_messages(self):
        st = self.st
        for m in MESSAGES:
            if m["id"] in st["messages"]:
                continue
            w = m.get("when", {})
            if w.get("flag") and not self.has(w["flag"]):
                if w["flag"] not in st["messages"] or st["msg_turn"].get(w["flag"], st["turn"]) >= st["turn"]:
                    continue
            if w.get("read") and w["read"] not in st["read"]:
                continue
            st["messages"].append(m["id"])
            st["msg_turn"][m["id"]] = st["turn"]
            self.say("", "✉ %s：" % m["from"], *["　" + x for x in m["text"].splitlines()])
            return          # 一次只来一条

    # —— 指令 ——
    def c_help(self, arg):
        return HELP

    def c_status(self, arg):
        st = self.st
        n = NODES[st["loc"]]
        L = ["【案卷 #0917】N-17（小满）" + ("　已结案" if st["submitted"] else ""),
             "你在：%s" % n["name"],
             "去得了的地方：" + "、".join(NODES[k]["name"] for k in st["nodes"])]
        if st["name"]:
            L.insert(0, "调查员：%s" % st["name"])
        return "\n".join(L)

    def c_name(self, arg):
        if not arg:
            return Fail("name <名字>")
        self.st["name"] = " ".join(arg)[:16]
        return "调查员登记名：%s。" % self.st["name"]

    def c_look(self, arg):
        st = self.st
        node = NODES[st["loc"]]
        if arg:
            q = " ".join(arg)
            objs = node.get("objects", {})
            k = self.resolve(q, objs)
            if k:
                o = objs[k]
                state = "（已经打开了）" if o.get("lock") in st["opened"] else ""
                return "【%s】%s%s" % (k, o["text"], state)
            e = self.resolve(q, EVIDENCE, self.visible_here())
            if e:
                return self.c_read([q])
            return Fail("这里看不到这个。")
        L = ["【%s】%s" % (node["name"], node["desc"])]
        here = self.visible_here()
        if here:
            L.append("这里有：" + "、".join(EVIDENCE[k]["name"] for k in here))
        if node.get("objects"):
            L.append("还可以细看：" + "、".join(node["objects"]))
        people = [p["name"] for p in PEOPLE.values() if p["node"] == st["loc"]]
        if people:
            L.append("可以说话的人：" + "、".join(people) + "（ask <人> <话题>）")
        return "\n".join(L)

    def c_goto(self, arg):
        st = self.st
        k = self.resolve(" ".join(arg), NODES, st["nodes"])
        if not k:
            return Fail("去不了那里。status 看看能去哪。")
        if k == st["loc"]:
            return "你已经在%s了。" % NODES[k]["name"]
        st["loc"] = k
        L = [self.c_look([])]
        if k == "channel" and "greeted" not in st["flags"]:
            self.flag("greeted")
            L.append(PEOPLE["ayuan"]["greet"])
            L.append("（ask 阿沅 <话题>。比如问她：小满、那天晚上……）")
        return "\n".join(L)

    def c_read(self, arg):
        st = self.st
        q = " ".join(arg)
        pool = list(dict.fromkeys(self.visible_here() + st["read"]))
        k = self.resolve(q, self.ev_table(), pool)
        if not k:
            return Fail("没有这份证据，或者它不在这里。look 看看这里有什么，list 看看你手上有什么。")
        e = self.ev(k)
        new = k not in st["read"]
        if new:
            st["read"].append(k)
            if e.get("private"):
                st["dark"]["absorb"] += 1
        L = ["▣ %s" % e["name"]] + ["　" + x for x in e["text"].splitlines()]
        return "\n".join(L)

    def c_list(self, arg):
        st = self.st
        if not st["read"]:
            return "你手上还没有证据。"
        L = ["【你手上的证据】"]
        for i, k in enumerate(st["read"], 1):
            L.append(" %d. %s" % (i, self.ev(k)["name"]))
        if st["restored"]:
            L.append("【还原出来的】")
            L += [" · %s" % RESTORES[r]["name"] for r in st["restored"]]
        return "\n".join(L)

    def pick(self, q):
        """手上的证据：编号或名字。"""
        st = self.st
        if q.isdigit() and 1 <= int(q) <= len(st["read"]):
            return st["read"][int(q) - 1]
        return self.resolve(q, self.ev_table(), st["read"])

    def c_compare(self, arg):
        st = self.st
        if len(arg) < 2:
            return Fail("compare <证据> <证据>：把两份手上的证据放在一起看。")
        a, b = self.pick(arg[0]), self.pick(arg[1])
        if not a or not b:
            return Fail("要比对的证据得先在你手上。list 看看。")
        if a == b:
            return Fail("同一份证据。")
        for ln in LINKS:
            if {ln["a"], ln["b"]} == {a, b}:
                if ln["id"] in st["links"]:
                    return "这两份已经对上过了。"
                st["links"].append(ln["id"])
                self.flag(ln["id"])
                self.flag(ln.get("flag"))
                L = ["◆ 关键发现！" if ln.get("key") else "◇ 对上了。"]
                L += ["　" + x for x in ln["text"].splitlines()]
                if ln.get("unlock_node") and ln["unlock_node"] not in st["nodes"]:
                    st["nodes"].append(ln["unlock_node"])
                    L.append("🗺 现在可以去：%s" % NODES[ln["unlock_node"]]["name"])
                return "\n".join(L)
        return "你把两份放在一起看了很久。至少现在，看不出它们有什么关系。"

    def c_restore(self, arg):
        st = self.st
        if not arg:
            return Fail("restore <碎片> <碎片> ...：把残缺的东西拼回去。")
        got = [self.pick(x) for x in arg]
        if not all(got):
            return Fail("要拼的碎片得先在你手上。list 看看。")
        got = set(got)
        for rid, r in RESTORES.items():
            need = set(r["frags"])
            if got == need:
                if rid in st["restored"]:
                    return "已经拼好了。list 里能看到。"
                st["restored"].append(rid)
                st["dark"]["absorb"] += 2
                self.flag(rid)
                self.flag(r.get("flag"))
                return "\n".join(["◆ 关键发现！拼上了：%s" % r["name"]] + ["　" + x for x in r["text"].splitlines()])
            if got < need:
                return "拼得上，但是不完整。中间还缺着什么。"
        return "这些拼不到一起。"

    def c_open(self, arg):
        st = self.st
        if len(arg) < 2:
            return Fail("open <东西> <口令>")
        objs = NODES[st["loc"]].get("objects", {})
        k = self.resolve(arg[0], objs)
        if not k or not objs[k].get("lock"):
            return Fail("这里没有能打开的这个。")
        lid = objs[k]["lock"]
        if lid in st["opened"]:
            return "已经打开了。"
        lock = LOCKS[lid]
        phrase = " ".join(arg[1:]).strip().strip("“”\"'")
        if phrase not in lock["phrase"]:
            return Fail(lock["wrong"])
        st["opened"].append(lid)
        if lock.get("voice"):
            st["dark"]["voice"] += 1
        inside = [e["name"] for e in EVIDENCE.values() if e.get("in") == lid]
        return lock["open"] + ("\n里面有：" + "、".join(inside) if inside else "")

    def c_ask(self, arg):
        st = self.st
        if not arg:
            return Fail("ask <人> <话题>")
        here = [k for k, p in PEOPLE.items() if p["node"] == st["loc"]]
        pid = self.resolve(arg[0], PEOPLE, here)
        if not pid:
            return Fail("这里没有这个人。")
        p = PEOPLE[pid]
        if len(arg) < 2:
            return "（问她什么？比如：%s）" % "、".join(list(p["topics"])[:2])
        tid = self.resolve(" ".join(arg[1:]), p["topics"])
        if not tid:
            return p["unknown"]
        t = p["topics"][tid]
        key = pid + ":" + tid
        if key not in st["asked"]:
            st["asked"].append(key)
            st["dark"]["human"] += 1
        L = [t["text"]]
        if t.get("evidence") and t["evidence"] not in st["read"]:
            st["read"].append(t["evidence"])
            L.append("（记下了：%s）" % TESTIMONY[t["evidence"]]["name"])
        return "\n".join(L)

    # —— 结案报告 ——
    def field_ok(self, f):
        need = f.get("need")
        return not need or any(self.has(x) for x in need.get("any", []))

    def opts_for(self, f):
        o = []
        for x in f["opts"]:
            n = f.get("opt_need", {}).get(x)
            if not n or self.has(n):
                o.append(x)
        return o

    def c_report(self, arg):
        st = self.st
        fields = [f for f in REPORT["fields"] if self.field_ok(f)]
        if not arg:
            L = [REPORT["header"]]
            for f in fields:
                L.append("　%s：%s　（可填：%s）" % (f["name"], st["report"].get(f["id"], "——"), " / ".join(self.opts_for(f))))
            L.append("（report <栏目> <值> 填写；report submit 提交）" if not st["submitted"] else REPORT["submitted"])
            return "\n".join(L)
        if st["submitted"]:
            return Fail("报告已经提交了。")
        if arg[0] in ("submit", "提交"):
            return self.submit(fields)
        f = next((f for f in fields if arg[0] in f["name"] or arg[0] == f["id"]), None)
        if not f:
            return Fail("没有这一栏。report 看看有哪些。")
        if len(arg) < 2:
            return Fail("%s 填什么？可填：%s" % (f["name"], " / ".join(self.opts_for(f))))
        q = " ".join(arg[1:])
        opts = self.opts_for(f)
        v = next((x for x in opts if x == q), None) or next((x for x in opts if q in x), None)
        if not v:
            return Fail("这一栏现在只能填：%s" % " / ".join(opts))
        st["report"][f["id"]] = v
        return "%s：%s" % (f["name"], v)

    def submit(self, fields):
        st = self.st
        if "status" not in st["report"]:
            return Fail(REPORT["need_to_submit"])
        st["submitted"] = True
        vals = [st["report"].get(f["id"]) for f in fields]
        st["dark"]["unknown"] += sum(1 for v in vals if v in (None, "未知"))
        if st["report"]["status"] == "存活":
            st["dark"]["alive"] += 1
        L = [REPORT["submitted"]]
        if len(fields) == len(REPORT["fields"]) and all(v and v != "未知" for v in vals):
            L.append(REPORT["complete"])
        L += ["", AFTER_REPORT["killer"]]
        d = st["dark"]
        if d["absorb"] >= 3 and d["absorb"] >= d["human"]:
            L.append(AFTER_REPORT["killer_absorb"])
        elif d["human"] >= 2:
            L.append(AFTER_REPORT["killer_human"])
        L += ["", AFTER_REPORT["ayuan"]]
        self.ask(AFTER_REPORT["ask"])
        L.append(self.ask_text())
        return "\n".join(L)

    # —— 选择 ——
    def ask(self, qid):
        self.st["pending"], self.st["ask"] = "ask", qid

    def ask_text(self):
        q = ASKS[self.st["ask"]]
        return "\n".join(["", q["text"]] + ["  %d. %s" % (i, o["t"]) for i, o in enumerate(q["opts"], 1)])

    def c_choose(self, arg):
        st = self.st
        try:
            k = int(arg[0])
        except (IndexError, ValueError):
            return Fail("choose <编号>")
        if st["pending"] == "consent":
            if k == 1:
                st["pending"], st["accepted"] = None, True
                return "\n".join(INTRO + ["", self.c_look([]), "", "（help 看看能做什么。先读一读案卷吧。）"])
            if k == 2:
                st["pending"], st["accepted"], st["ended"] = None, False, "declined"
                return DECLINED
            return Fail("choose 1 或 choose 2")
        if st["pending"] != "ask":
            return Fail("现在没有要选的。")
        opts = ASKS[st["ask"]]["opts"]
        if not 1 <= k <= len(opts):
            return Fail("choose 1 到 %d" % len(opts))
        o = opts[k - 1]
        self.flag(o.get("flag"))
        st["dark"]["human"] += o.get("human", 0)
        st["pending"], st["ask"] = None, None
        st["ended"] = "case1"
        return o["r"] + "\n\n" + CASE_END

    # —— 其他 ——
    def c_messages(self, arg):
        st = self.st
        got = [m for m in MESSAGES if m["id"] in st["messages"]]
        if not got:
            return "没有消息。"
        L = []
        for m in got:
            L += ["✉ %s：" % m["from"]] + ["　" + x for x in m["text"].splitlines()]
        return "\n".join(L)

    def c_journal(self, arg):
        st = self.st
        if arg and arg[0] == "add":
            text = " ".join(arg[1:]).strip()
            if not text:
                return Fail("journal add <想写的话>")
            st["journal"].append({"t": st["turn"], "text": text[:300]})
            self.note_text(" " + text + " ")
            return "📝 写下了：“%s”" % text[:300]
        if not st["journal"]:
            return "调查日志还是空的。journal add <文字> 写一句。"
        return "\n".join(["【调查日志】"] + ["· " + j["text"] for j in st["journal"]])

    def c_bar(self, arg):
        self.st["nobar"] = bool(arg and arg[0] in ("off", "关"))
        return "状态栏关了。" if self.st["nobar"] else "状态栏开着。"

    def c_quit(self, arg):
        self.st["flags"]["quit"] = self.st["turn"]
        return "你从终端前站起来。案卷还开着，存档还在。想回来的时候，接着输入指令就好。"

    def bar(self):
        st = self.st
        return "📡 " + json.dumps({"case": "0917", "loc": NODES[st["loc"]]["name"], "pending": st["pending"],
                                  "ended": st["ended"], "v": VERSION}, ensure_ascii=False)

    # —— 执行 ——
    def run(self, c):
        st = self.st
        parts = c.strip().split()
        if not parts:
            return None
        verb = ALIAS.get(parts[0], parts[0].lower())
        arg = parts[1:]
        fn = getattr(self, "c_" + verb, None)
        if not fn:
            return Fail("没有这个指令。help 看看。")
        if st["ended"] and verb not in READ_ONLY | {"quit"}:
            return Fail("这一局到这里了。journal、list、messages 还能看；new; confirm 重新开始。")
        if st["pending"] == "consent" and verb not in ("choose", "help", "quit", "bar"):
            return Fail("先决定玩不玩：choose 1 开始，choose 2 不玩了。")
        if st["pending"] == "ask" and verb not in PENDING_OK:
            return Fail("阿沅在等你。choose <编号>。")
        return fn(arg)

    def save(self):
        p = save_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.st, f, ensure_ascii=False)
        os.replace(tmp, p)


_G = None


def _load():
    global _G
    if _G is not None:
        return _G
    p = save_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                _G = Game(json.load(f))
                return _G
        except (OSError, ValueError):
            shutil.copy(p, p + ".broken")
    _G = Game.fresh()
    _G.st["intro_pending"] = True
    _G.save()
    return _G


def new_game(name=None, force=False):
    """开一局新游戏。已有存档时需要 force=True（旧档会备份成 .bak）。"""
    global _G
    p = save_path()
    if os.path.exists(p):
        if not force:
            return "已经有存档了。要重新开始：new_game(force=True)，或 cmd(\"new; confirm\")。"
        shutil.copy(p, p + ".bak")
    _G = Game.fresh(name)
    _G.save()
    return TITLE + "\n\n" + CONSENT


def cmd(text):
    """执行一条或多条指令（英文分号分隔），返回要显示的文字。"""
    g = _load()
    g.out = []
    if g.st.pop("intro_pending", False):
        g.say(TITLE, "", CONSENT, "")
    allc = [x for x in text.split(";")]
    if [x.strip().lower() for x in allc][:1] == ["new"] or (allc and allc[0].strip().lower() == "new"):
        if "confirm" in [x.strip().lower() for x in allc]:
            return new_game(force=True)
        g.say("要重新开始吗？确认请输入：new; confirm（旧存档会备份成 %s.bak）" % save_path())
        return "\n".join(g.out)
    cmds = [x for x in allc if x.strip()][:MAX_BATCH]
    for i, c in enumerate(cmds):
        g.st["turn"] += 1
        if len(cmds) > 1:
            g.say("▶ " + c.strip())
        res = g.run(c)
        if res:
            g.say(res)
        g.check_messages()
        if isinstance(res, Fail) and i < len(cmds) - 1:
            g.say("（后面的指令先停下了。）")
            break
        if g.st["pending"] and i < len(cmds) - 1 and not c.strip().startswith(("choose", "help", "status")):
            g.say("（有事要你决定，后面的指令先停下了。）")
            break
    g.save()
    if not g.st.get("nobar") and os.environ.get("PIER7_BAR", "1") != "0":
        g.say(g.bar())
    return "\n".join(g.out)


_DATA = (
    "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"
    "xv6in6AxPD8M3YQ9PHr03zBp7nU="
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    argv = sys.argv[1:]
    if argv[:1] == ["cmd"]:
        print(cmd(" ".join(argv[1:])))
    else:
        print(cmd("help"))
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if line.strip() in ("exit",):
                break
            print(cmd(line))

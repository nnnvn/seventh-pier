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

VERSION = "0.1.1"

DATA_NAMES = ("TITLE", "CONSENT", "DECLINED", "INTRO", "HELP", "NODES", "EVIDENCE", "LINKS", "NEAR", "RESTORES",
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
            r = self.resolve(q, RESTORES, st["restored"])
            if r:
                return "\n".join(["▣ %s（还原）" % RESTORES[r]["name"]] + ["　" + x for x in RESTORES[r]["text"].splitlines()])
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
            L.append("（read <名字> 可以重读）")
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
        for nr in NEAR:
            if {nr["a"], nr["b"]} == {a, b}:
                return "\n".join(["◇ 有关系，但还不够下结论。"] + ["　" + x for x in nr["text"].splitlines()])
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
                    return "\n".join(["▣ %s（已经拼好了）" % r["name"]] + ["　" + x for x in r["text"].splitlines()])
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
            if st.get("report_reason"):
                L.append("　理由：%s" % st["report_reason"])
            L.append("（report <栏目> <值> 填写；report submit 提交）" if not st["submitted"] else REPORT["submitted"])
            if not st["submitted"]:
                L.append(REPORT["reason"])
            return "\n".join(L)
        if st["submitted"]:
            return Fail("报告已经提交了。")
        if arg[0] in ("submit", "提交"):
            return self.submit(fields)
        if arg[0] in ("理由", "reason", "说明"):
            text = " ".join(arg[1:]).strip()
            if not text:
                return Fail("report 理由 <一句话>")
            st["report_reason"] = text[:200]
            self.note_text(" " + text + " ")
            return "理由：%s" % text[:200]
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
        L += ["", AFTER_REPORT["killer"], AFTER_REPORT["by_status"][st["report"]["status"]]]
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


def verb_of(c):
    parts = c.strip().split()
    return ALIAS.get(parts[0], parts[0].lower()) if parts else ""


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
    allcmds = [x for x in allc if x.strip()]
    cmds = allcmds[:MAX_BATCH]
    for i, c in enumerate(cmds):
        g.st["turn"] += 1
        if len(cmds) > 1:
            g.say("▶ " + c.strip())
        verb = verb_of(c)
        res = g.run(c)
        if res:
            g.say(res)
        if verb in ("read", "compare", "restore", "open", "ask", "goto") and not isinstance(res, Fail):
            g.check_messages()
        if verb == "quit" and i < len(cmds) - 1:
            g.say("（你离开了，后面的指令没有执行。）")
            break
        if isinstance(res, Fail) and i < len(cmds) - 1:
            g.say("（后面的指令先停下了。）")
            break
        if g.st["pending"] and i < len(cmds) - 1 and verb not in ("choose", "help", "status"):
            g.say("（有事要你决定，后面的指令先停下了。）")
            break
    if len(allcmds) > MAX_BATCH:
        g.say("（一次最多 %d 条。后面 %d 条没有执行。）" % (MAX_BATCH, len(allcmds) - MAX_BATCH))
    g.save()
    if not g.st.get("nobar") and os.environ.get("PIER7_BAR", "1") != "0":
        g.say(g.bar())
    return "\n".join(g.out)


_DATA = (
    "eNrFW21PW9eW/itHnq9p1bRXzUymipSmzEx0e5MqyYw0airkECfhBmyucZqJqlzZEILNmyGQAAbCS3gLLzYECsYGLN35J6n38fGn"
    "/oV51lr77LMNJO1cjTSqS4732WefvfZa61nPWnv7p9Ctq7e+bQpdDP2S7KttbFSL3bW5lFrc/SXZHzoXunL92s2ma7dw26tMuhOF"
    "ajFZLa7VypPO5atObehtLffMTQ27yVfuq161Oe4Wi246+0uy63b0dlTlu6vFQTU8Wi2Vfj0ccN/Nu9MZbz6J6+rBrHytraVqz/a8"
    "/R3VW3JnljBctTjtLVV4CBrgMFcfz9NbS1OqZ6daGvJK69Xy0S/JlJffUpXn6sWAGk6rgz08ip71hQkM7yUHMF08Ii21oS01vSov"
    "xFy87iM19bq2OYaeaqDH7d+oHs3xCyGQSm/hbWqwj2Z5NKeyBXTHLNRhUq30u8lk7dWITIfuHuy443sQUtpp9qV+Lb08XC0v4TbG"
    "RSuElpZ6LovHvONR1bPk/OVRa8KpLZfxAv2k45z/1JH30ZfPP3VkiGrpOTTyTdOVb69ea/oGKlFLWIeu+vgqrUyxD/fxVS13ua+n"
    "1eiaWnzrdu9AUqwrrYm8Lhp5/M9OSyx6rzXejt4Y8Oq1Wzeuhy5+H7K1T3pdnVev+6FzrJ3qHXAnV6vFTK3rQA2m0E2ln6vFbS81"
    "hp4yjqyvyr90pwN1q8MuVSx6hX13uytoTM/VJxdJf0cvacb5bpVdoxelpt3NBdGNjKn2l2qr/aqYrvfCkAqQEHNwNzDIoOqbpwHf"
    "JN3ZJbS7mSRGdloeRFoedsRaowm6OZ1Uw0Nm+mpqiqafHYE98EjJannPndpVS71uck2U9z45hg9mRAvwqhcd6t1HXmUCg2gpZ7ox"
    "F9MiRmi+1sZyXu+OO7FKiz+Zg1JIuBcDtbFVN70v88F7IYG3lfYKM2zvfdViP96FCeNdKj/gvtytVmbU5kRtJqmXYaxQ69/Q/jS9"
    "KopGOz4wM6yct9WNZVDbSQyor0fYEXZnvd03pKjimugTjiM6sZbKoRlm0u70hre1Xdspy+DkQXhqPq02h93dY/g83kWrCkVNzVWL"
    "JbLwFwPOtU/OX3BUzzZWz6v0qp5NdbBLI/xwLvRvTd9+x8gChBiqHqTZD0bg2Is5d2beHcD6LmJpvP5tgg9YVHYff+s58kxZJlqy"
    "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"
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

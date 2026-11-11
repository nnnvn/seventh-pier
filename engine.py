# -*- coding: utf-8 -*-
"""《第七码头》引擎。

只用标准库。所有文字内容在 content.py（打包后在 _DATA 数据块里），这里只有规则。
本文件没有 exec / eval / 联网 / 子进程；唯一的文件读写是存档。
"""
import json
import os
import shutil

VERSION = "0.2.0"

DATA_NAMES = ("TITLE", "CONSENT", "DECLINED", "INTRO", "HELP", "NODES", "EVIDENCE", "LINKS", "NEAR", "RESTORES",
              "LOCKS", "PEOPLE", "TESTIMONY", "REPORT", "MESSAGES", "AFTER_REPORT", "ASKS", "CASE_END", "TICS", "READY",
              "CASES", "REPORT_COMMON")


def load_content(d):
    globals().update({k: v for k, v in d.items() if k in DATA_NAMES})


# >>> DEV-ONLY: 开发时从 content.py 读内容；build.py 会删掉这一段
try:
    import content as _content
    load_content(_content.DATA)
except ImportError:
    pass
# <<< DEV-ONLY


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
            "nodes": [k for k, n in NODES.items() if not n.get("hidden") and n.get("case", "c1") == "c1"],
            "read": [], "links": [], "restored": [], "opened": [], "asked": [], "flags": {},
            "report": {}, "submitted": False, "messages": [], "msg_turn": {}, "journal": [],
            "dark": {"absorb": 0, "voice": 0, "drift": 0, "alive": 0, "unknown": 0, "human": 0},
            "pending": "consent", "ask": None, "ended": None, "nobar": False, "turn": 0,
            "case": "c1", "reports": {}, "reasons": {},
        }
        return Game(st)

    def migrate(self):
        st = self.st
        st.setdefault("case", "c1")
        st.setdefault("reports", {})
        st.setdefault("reasons", {})
        if st.get("ended") == "case1":      # 0.1.x 的存档：第一案结束了，接着进第二案
            st["ended"] = None
            self.end_case_and_next()
            st["resume_note"] = True

    # —— 案子 ——
    @staticmethod
    def case_of(eid):
        e = EVIDENCE.get(eid) or TESTIMONY.get(eid) or {}
        return e.get("case") or eid.split("_")[0]

    def case_idx(self, c):
        return list(CASES).index(c) if c in CASES else 0

    def in_reach(self, c):
        return self.case_idx(c) <= self.case_idx(self.st["case"])

    def current_first(self, pool):
        cur = self.st["case"]
        return sorted(pool, key=lambda k: 0 if self.case_of(k) == cur else 1)

    def end_case_and_next(self):
        """当前这一案结束：报告归档，下一案开始。返回要显示的文字。"""
        st = self.st
        cur = st["case"]
        st["reports"][cur] = st["report"]
        if st.get("report_reason"):
            st["reasons"][cur] = st.pop("report_reason")
        nxt = CASES[cur].get("next")
        if not nxt:
            st["ended"] = cur
            return ""
        st["case"], st["report"], st["submitted"] = nxt, {}, False
        st["loc"] = "office"
        for k, n in NODES.items():
            if n.get("case") == nxt and not n.get("hidden") and k not in st["nodes"]:
                st["nodes"].append(k)
        return CASES[nxt].get("start", "")

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
        """这个地方能看到、还没被锁住的证据（只到当前这一案为止）。"""
        st = self.st
        return self.current_first([k for k, e in EVIDENCE.items()
                                   if e["node"] == st["loc"] and self.in_reach(self.case_of(k))
                                   and (not e.get("in") or e["in"] in st["opened"])])

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
            if m.get("case", "c1") != st["case"]:
                continue
            w = m.get("when", {})
            if w.get("any") and not any(self.has(x) for x in w["any"]):
                continue
            if w.get("flag") and not self.has(w["flag"]):
                if w["flag"] not in st["messages"] or st["msg_turn"].get(w["flag"], st["turn"]) >= st["turn"]:
                    continue
            if w.get("read") and w["read"] not in st["read"]:
                continue
            st["messages"].append(m["id"])
            st["msg_turn"][m["id"]] = st["turn"]
            text = m["text"]
            if m.get("quote"):
                q = self.player_words()
                text = text.replace("{quote}", q) if q else m["text_none"]
                st.setdefault("msg_text", {})[m["id"]] = text
            self.say("", "✉ %s：" % m["from"], *["　" + x for x in text.splitlines()])
            return          # 一次只来一条

    def player_words(self):
        """玩家自己写下的最近一句：日志，或者上一案的结案理由。"""
        st = self.st
        if st["journal"]:
            return st["journal"][-1]["text"][:60]
        for c in reversed(list(CASES)):
            if st["reasons"].get(c):
                return st["reasons"][c][:60]
        return None

    # —— 指令 ——
    def c_help(self, arg):
        return HELP

    def c_status(self, arg):
        st = self.st
        n = NODES[st["loc"]]
        cs = CASES[st["case"]]
        L = ["【案卷 #%s】%s" % (cs["no"], cs["victim"]) + ("　已结案" if st["submitted"] else ""),
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
        for pid, p in PEOPLE.items():
            g = "greeted" if pid == "ayuan" else "greeted_" + pid
            if p["node"] == k and g not in st["flags"]:
                self.flag(g)
                L.append(p["greet"])
                hints = self.topic_hints(p, pid)
                hinted = st.setdefault("hinted_by", {}).setdefault(pid, [])
                hinted.extend(h for h in hints if h not in hinted)
                L.append("（ask %s <话题>。现在可以问：%s）" % (p["name"], "、".join(hints)))
        return "\n".join(L)

    def c_read(self, arg):
        st = self.st
        q = " ".join(arg)
        k = self.resolve(q, self.ev_table(), self.visible_here()) or \
            self.resolve(q, self.ev_table(), self.current_first(st["read"]))
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
            self.flag(e.get("flag"))
        L = ["▣ %s" % e["name"]] + ["　" + x for x in e["text"].splitlines()]
        u = e.get("unlock")
        if u and u not in st["nodes"]:
            st["nodes"].append(u)
            L.append("🗺 现在可以去：%s" % NODES[u]["name"])
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
        return self.resolve(q, self.ev_table(), self.current_first(st["read"])) or \
            self.resolve(q, RESTORES, st["restored"])

    def c_compare(self, arg):
        st = self.st
        if len(arg) < 2:
            return Fail("compare <证据> <证据>：把两份手上的证据放在一起看。")
        a, b = self.pick(arg[0]), self.pick(arg[1])
        if not a or not b:
            return Fail("要比对的证据得先在你手上（还原出来的也可以）。list 看看。")
        if a == b:
            return Fail("同一份证据。")
        for ln in LINKS:
            if {ln["a"], ln["b"]} == {a, b}:
                if ln["id"] in st["links"]:
                    return "这两份已经对上过了。"
                st["links"].append(ln["id"])
                self.flag(ln["id"])
                self.flag(ln.get("flag"))
                self.flag(ln.get("flag2"))
                L = ["◆ 关键发现！" if ln.get("key") else "◇ 对上了。"]
                L += ["　" + x for x in ln["text"].splitlines()]
                L += self.ready_hint()
                if ln.get("unlock_node") and ln["unlock_node"] not in st["nodes"]:
                    st["nodes"].append(ln["unlock_node"])
                    L.append("🗺 现在可以去：%s" % NODES[ln["unlock_node"]]["name"])
                return "\n".join(L)
        for nr in NEAR:
            if {nr["a"], nr["b"]} == {a, b}:
                text = nr["text_known"] if nr.get("known") and self.has(nr["known"]) else nr["text"]
                return "\n".join(["◇ 有关系，但还不够下结论。"] + ["　" + x for x in text.splitlines()])
        return "你把两份放在一起看了很久。至少现在，看不出它们有什么关系。"

    def c_restore(self, arg):
        st = self.st
        if not arg:
            return Fail("restore <碎片> <碎片> ...：把残缺的东西拼回去。")
        got = [self.pick(x) for x in arg]
        if not all(got) or any(g in RESTORES for g in got):
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
                return "\n".join(["◆ 关键发现！拼上了：%s" % r["name"]] + ["　" + x for x in r["text"].splitlines()]
                                 + self.ready_hint())
            if got < need:
                return "拼得上，但是不完整。中间还缺着什么。"
        return "这些拼不到一起。"

    def ready_hint(self):
        cur = self.st["case"]
        key = "ready" if cur == "c1" else "ready_" + cur
        if self.has(CASES[cur]["lead"]) and key not in self.st["flags"] and not self.st["submitted"]:
            self.flag(key)
            return ["", READY]
        return []

    def topic_hints(self, p, pid=None):
        st, out = self.st, []
        mine = [a for a in st["asked"] if pid and a.startswith(pid + ":")] if pid else st["asked"]
        for name, cond in p.get("hints", []):
            ok = cond is None
            if cond and cond.startswith("read:"):
                ok = cond[5:] in st["read"]
            elif cond and cond.startswith("flag:"):
                ok = self.has(cond[5:])
            elif cond and cond.startswith("asked:"):
                ok = len(mine) >= int(cond[6:])
            if ok and name not in out:
                out.append(name)
        return out

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
        here = [k for k, p in PEOPLE.items() if p["node"] == st["loc"] and self.in_reach(p.get("case", "c1"))]
        pid = self.resolve(arg[0], PEOPLE, here)
        if not pid:
            return Fail("这里没有这个人。")
        p = PEOPLE[pid]
        if len(arg) < 2:
            now = self.topic_hints(p, pid)
            hb = st.setdefault("hinted_by", {})
            hb[pid] = list(dict.fromkeys(hb.get(pid, list(st.get("hinted", [])) if pid == "ayuan" else []) + now))
            return "（现在可以问：%s）" % "、".join(now)
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
        hinted = st.setdefault("hinted_by", {}).setdefault(pid, list(st.get("hinted", [])) if pid == "ayuan" else [])
        now = self.topic_hints(p, pid)
        new = [h for h in now if h not in hinted]
        if new and hinted:
            L.append("（现在也可以问：%s）" % "、".join(new))
        st["hinted_by"][pid] = list(dict.fromkeys(hinted + now))
        return "\n".join(L)

    # —— 结案报告 ——
    def field_ok(self, f):
        need = f.get("need")
        return not need or any(self.has(x) for x in need.get("any", []))

    def opts_for(self, f):
        o = []
        for x in f["opts"]:
            n = f.get("opt_need", {}).get(x)
            if isinstance(n, str):
                n = [n]
            if not n or any(self.has(y) for y in n):
                o.append(x)
        return o

    def rep(self):
        return REPORT[self.st["case"]]

    def c_report(self, arg):
        st = self.st
        R = self.rep()
        fields = [f for f in R["fields"] if self.field_ok(f)]
        if not arg:
            L = [R["header"]]
            for f in fields:
                L.append("　%s：%s　（可填：%s%s）" % (f["name"], st["report"].get(f["id"], "——"), " / ".join(self.opts_for(f)),
                                                  "，可以多选，用空格隔开" if f.get("multi") else ""))
            if st.get("report_reason"):
                L.append("　理由：%s" % st["report_reason"])
            L.append("（report <栏目> <值> 填写；report submit 提交）" if not st["submitted"] else REPORT_COMMON["submitted"])
            if not st["submitted"]:
                L.append(REPORT_COMMON["reason"])
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
        opts = self.opts_for(f)
        qs = arg[1:] if f.get("multi") else [" ".join(arg[1:])]
        vals = []
        for q in qs:
            v = next((x for x in opts if x == q), None) or next((x for x in opts if q in x), None)
            if not v:
                return Fail("这一栏现在只能填：%s" % " / ".join(opts))
            if v not in vals:
                vals.append(v)
        if len(vals) > 1 and "未知" in vals:
            vals.remove("未知")
        st["report"][f["id"]] = "、".join(vals)
        return "%s：%s" % (f["name"], st["report"][f["id"]])

    def rule_ok(self, c):
        st, d = self.st, self.st["dark"]
        if "read" in c and c["read"] not in st["read"]:
            return False
        if "absorb" in c and d["absorb"] < c["absorb"]:
            return False
        if c.get("absorb_ge_human") and d["absorb"] < d["human"]:
            return False
        if "human" in c and d["human"] < c["human"]:
            return False
        if "asked" in c and len([a for a in st["asked"] if a.startswith(c["asked"][0] + ":")]) < c["asked"][1]:
            return False
        return True

    def submit(self, fields):
        st = self.st
        R = self.rep()
        A = AFTER_REPORT[st["case"]]
        if "status" not in st["report"]:
            return Fail(REPORT_COMMON["need_to_submit"])
        st["submitted"] = True
        vals = [st["report"].get(f["id"]) for f in fields]
        st["dark"]["unknown"] += sum(1 for v in vals if v in (None, "未知"))
        for f in fields:
            if st["report"].get(f["id"]) in f.get("alive", []):
                st["dark"]["alive"] += 1
        L = [REPORT_COMMON["submitted"]]
        if len(fields) == len(R["fields"]) and all(v and v != "未知" for v in vals):
            L.append(REPORT_COMMON["complete"])
        L += ["", A["killer"]]
        for fid, lines in A["by"].items():
            v = st["report"].get(fid)
            if v in lines:
                L.append(lines[v])
        if A.get("quote_reason") and st.get("report_reason"):
            L.append(A["quote_reason"].replace("{quote}", st["report_reason"][:60]))
        for r in A.get("rules", []):
            if self.rule_ok(r["if"]):
                L.append(r["text"])
                break
        L += ["", A["ayuan"]]
        self.ask(A["ask"])
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
        cur = st["case"]
        start = self.end_case_and_next()
        L = [o["r"], "", CASE_END[cur]]
        if start:
            L += ["", start]
        return "\n".join(L)

    # —— 其他 ——
    def c_messages(self, arg):
        st = self.st
        got = [m for m in MESSAGES if m["id"] in st["messages"]]
        if not got:
            return "没有消息。"
        L = []
        for m in got:
            text = st.get("msg_text", {}).get(m["id"], m["text"])
            L += ["✉ %s：" % m["from"]] + ["　" + x for x in text.splitlines()]
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
        return "📡 " + json.dumps({"case": CASES[st["case"]]["no"], "loc": NODES[st["loc"]]["name"], "pending": st["pending"],
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
            return Fail("%s在等你。choose <编号>。" % ASKS[st["ask"]].get("who", "有人"))
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
                _G.migrate()
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
    if g.st.pop("resume_note", False):
        g.say("（第一案已经结案了。游戏更新了，第二案到了。）", CASES[g.st["case"]].get("start", ""), "")
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

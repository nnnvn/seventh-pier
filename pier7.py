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

VERSION = "0.2.0"

DATA_NAMES = ("TITLE", "CONSENT", "DECLINED", "INTRO", "HELP", "NODES", "EVIDENCE", "LINKS", "NEAR", "RESTORES",
              "LOCKS", "PEOPLE", "TESTIMONY", "REPORT", "MESSAGES", "AFTER_REPORT", "ASKS", "CASE_END", "TICS", "READY",
              "CASES", "REPORT_COMMON")


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


_DATA = (
    "eNrVfWtTHEea7l+pYD/sF8Yh4Znxrs6EIzwys+sYj+2wtefEhq0gkGhJjKBb0yD7KBzeaIQRzR104yohJBBIiIssLEG3EBFn/4lN"
    "VTef/BfO87xvZlZWdYNkxdmzuzGM3J1VlZX55nt53ktmf9tw6qNTHzc3nGj4uTBYefJkf7u3cq8nXNz6uTDU0Nhw8tNPvmj+5BQu"
    "V/emo6mN/e3C/vbjSnk6+OCjoDL6qDLzfdQzHhVuR7f7w7XJaHs7Ko79XLj6VfarbLjeu789Eo7f2C+Vfnk5HP2wEM0NVBcK+Ly/"
    "M69fK497Kt8/r754FvaXojtL6G5/e666tCddsIOXMweT63xraTbse7ZfGq2WVvfLuz8Xeqrrm+HetfD6cDheDHee41HceXB/Ct1X"
    "C8MYLh7RlsroZji3oi/EWKq9u+Hs3craTdwZDvdFQ0/2d+/JCzGhsLiJt4Ujgxzl7r1wbAO3YxThy0K4PBQVCpXbEzocXt15Fk0+"
    "xyS1naMvDZnZ68P75SVcRr9oxaS15WBmDI9VX90I+5aCv11u7w4qD8t4gXkyCI6/E+j7+KXpnUC72C9dw4p82Hzy448+af4QSxIu"
    "gQ5XDyZXSJntQVzH1/Dh1ejuXHjjcbj4KOp9hpmCrqSJvi6b+eZ/BGdz2XPt+U7cjQ4/+uTU5582nPiywV99ruvKQnh3CGsO2oX9"
    "w9H0yv72QOXqTjjSg9vC4rVw8Wm15ybu1H6UvuH6rWguXu7w5dVwe7u68SJ6ejVuLN47mF7k+u3e4ojXe8Oxx3xRz1y0dl/XRvsM"
    "XyxVVobC7eJBPxhpAzPEGKIn6GQkHFxghw8K0fwS2qOBAnoOzl7InL14Kdee7ebFuUI4PuqGH87OcvhjE+AH6amwX34ezW6FS/1R"
    "4bEu3k+Fm/jDiEiA2/244aB3t7o3hU7MLO/0YiyuRZnQfa3cnKn2P4umVkj86RksCid3fbhycyUqvtDx4L2YQXWzWN24I/w+uL89"
    "hHdhwHhXuD4c3dra37sTrk1V7hQMGW5uVIaeGHmaW9GFRjv+wGagXHWzF2QInxbQofk8IYKwNV/desCF2n6s6wnB0TXxSBVwhAPF"
    "aO5JdfNp5VlZO6cE4amFYrg2Hm29gszjXaQqFmr23v52iRx+fTj45DfH3wvCvqegXnWvP+xbC3e22MPpxoZ/bv74M9Es0BCj+ztF"
    "kYMJCPbiTHRnIRoGfRdBmurQU6oPcNTYC/x7MEPJVDKRZIszwT8EuJ+s0jNDaSuO62pBLqvrj0DTgzv3uWDSIbgIBDWyVJkbwhir"
    "P27+8nKmI5e7GHyp+uV08H9eBOdz3bngD+HcZnR75302dHW3dl/u+ipb3eiJRtb5SHsXGKlIIg8MYaVIXnNtgA/kM61twR+0SXo4"
    "m+u81JrPxG32A3qJNm6GGzv724vQX64XvAzMMzqPl+UzXd05Plu5P1oZ6H8//vDOO+/w+cHBaH2o8rLktGQ09BLSEo6WpSMoAvSS"
    "u5TJBn/Q6+giHHsAmrwPfUQlPNPadREXSyUZ2B3oRg5MP1HJzC8d9NwIr03vlwtYLurHzaeQTbQE7kl5V6V8A5whg76Uy5NGoHQ0"
    "CLYbdKThheAP0b2xyiwJERZevu9d6Lp8prO9G7zQ93y/fBs9dWa6ulrPZ7o405vPoYe5os+LUc+G6fGvucv5bGtH8GVrG4iuBud9"
    "WcizF3K5LhLu5W2wkLwl29qJhnB8hDex4UxrPsidO8ePVLlkDl0ESoSwAD6opaIYiqLWqWsnQvTCQe8KOFQF5JNPP2z+ouHEtw3o"
    "tv1shp/4VtpKJ47lYmV1A/e2drS3dlHJuktodFfDwbthH+R7kULTluk6iz7Ac3il3oL3UQ5HXtDsvdio3HxKNTQ9zhGLQdbhUkRL"
    "JSg4JRsffzwJBqCe2ps+6B8OXxXD9YGDO9Oc5IPRaKAf94QPNg/mn9G2796r9j8OXzw1Kv27xoZL7Zn8e97EfBPhz8q1UBlUB69C"
    "z+JL9vh73oRET1CA5LKMaRbKXXVndW+8ujBMhrvWB80Xjtyjylx7AEWFxpOf/UsQzf5QKe3B4oavJsPeMaqHOwtooWGlqZ0wKn37"
    "cXCpPXueXUH1zt71mnPSDLxBLSwcrjikurFl5tvZns/n8t6ED27N8WXlG2Hphj9hbaf+tZfCRSifXW++apFU65KVJ5dTKlefILNx"
    "/lwfrvPwQDS5FM3NR6PXYd85DZ3s6HX+De6GTwdwmyXmwdUHvHjvZTTZU321I0ZvQOeriODgZg/54OUN2pPZdbW4GG7uzF8zZ7u7"
    "OFW9yE/xenr34+azrRg5p9aRO3sRUzt7vEWbGhu6M/+721vcyjJlSB8nk+HtYsR0GCB0NDVKYy+DlBWf+akwpUqKhmW9lxqfiGFL"
    "ZxeOLUE9oYefCtMN332HNTp7oTWbzXT4izS1F/3Q59g9sU5yibJmL4XLN6OBKQzSlzUVoo0dmmQrMvgKs+IwL6f1tMz1EHUQjq1q"
    "37xzaYdj3xyLygschRh7YylxaZCwFvJreCyf6Wi94o3+89+8C4C3Vt19Ulmd9seOC2Qw71L+XQ76QntbWybbcKI7fzmTmgKYmeMs"
    "b0WDi6KwzLOUtt0nEJLqwgr1wL1rWJawHwB2S1nTgTFlRNiC6saSwmWFj9AwmA4Wp7o3IatH+1Ipj8Hkm3llzucz3b6umN492Flz"
    "Ap9QF3KJq7JXrm5MuBvYeJrs1sUOzjY1uOn5NzrVQbl5MUE2m5oPi5MYX+URjSNJv2E0hEJPCJkI1vNw8fvK+DWALMVQ0BxwEago"
    "gKoEW6neMDjVIlGYCnlzdfl6dXzXSVh1ZKCysldZfypwxUhbjXi5uxISpq2cYL71HG70hKupxbQ56YpJ6b3RydabSBVJtTGBZ7G4"
    "KmEwbFT+s7Pk/LVJJ14drbm/tvu2DOZx7UFd4dJLlCn8W3/dfpVkaX+cWqGw/3JClwgrSTA9+YgULt+mKzZGD+zg4e3qxiTYD/wp"
    "HIixN//Pjz5s/uRkM4cPHXWuvUOtcq6N4zJmutHOTE1q8HfH/hF2ypuWtqPFXJBpnXaL4aNpEJqaj/BHhL6yvBlOvESrKgPFd8I6"
    "aPONp7DZfbgx2s9XWd83CG+MhCVg4Jlj755g52IU14EcS+BvQpEnD/e3fwAIG3weFXpwn7WktKGQeWHHkfDaiDIwOhenxfnMAhuc"
    "1zGjLgdJPXkv5ZoBnll1iRtV4REPy4pZPUfoOf199Izo0zkf+9ujvmcQuwXU4MdbutvPXjTa4pDFmVyG8xeO3Ar+rqnp3eP++mg7"
    "fTF7Dz6bm0y/8WrBX8SLw4ER489bh4WC8uMLVQeUA+lHZmyjCLKyAUC3zlMt0k+FOcra4qOfCneUunBGwKjSvqLt5OS9u5XyGqA+"
    "WpSMo/NwmtApddBwCWo43DSIBWummmh/b92BlYMCluVHA2IQuxDrjkuV2S0MBG8TT2IEU/hl9y7eqS/pe4ZJAFbIWhSV/CLmWCS8"
    "m4gW/rAECKLiRDgG5L91cHOa7rNMSmlUfdgDmYoGXuEe9F7TL8no9apRCLOuHbnz3qIqfIzXVLx2wJtwbzIhcLaFT8dLR+5vCgIl"
    "P109qBC4HXfgKxUtK4L3eNtvg0B9hnDxNiC64t/gOL1GGgPMpjTO8BRJOkNpfVRyz76HZ+sJ31cN+J+5Y39nCDdw+QTUqVGpPu/D"
    "ajprtF8ecUA2JgjQSvdRFPFeLEoywei2pdoziNVpEOzjM7elAlaWN2AlKlsbWC+jfGaAn6A4oX0qoIOspL2i611d7pFm149r/ioL"
    "KskCkCcStB+wM7t46YiZ+VC3OAl+9ycWX+Sc2A9siN4VTw7ew2+O/Z4coArqq6xpebem5bdxS2cmfz7T0t15qeXYu9K+uIxJB8fw"
    "qTgblkvVQl9l7RUwXJIRrAKWoIei55HwKZwH1WLxera1t+avHD5ti4MhLOHeVGIxbYv2AOwjUNk0Y9qX8u1ft3ZnLK5LLDHklLpg"
    "aYcLCA1SmA77yrB9DFWOPa4MrwpCW9XlZRxqeBUxD2oTEXn77FN4iBBqaA1c0viOollVH0s7Eq4s4RbqFhgBuFpL4MEtvEpvp5VA"
    "B3KJGkCYamkHbCc4kiNjHKH4lA9tUBVCYtAr1U1f0SAV4TDFmtG9FzoyPgVB732mrEwdpCEj4ToT8dIlyOcud/t23Thtbg3UQwYz"
    "IXYCiU8gFrlEibL3EFVLf0m9A84hLv/p2vXAN9tQFZ5qCYKDW3th6WHw+wA0dJrIPgj2Cl/8oBaVIYwjnjUzy3W0HTEvtaEwZfSM"
    "CjMpZeFa3G2EoegwnpgJ4XtWUOYm4UhZLiJMPypbnMEfOwbK+nGouqEGUhHPTHR1hQ7q7t1AmQxKkXG94f7o1rR/30+Fh/ijPG2I"
    "fwrmKj8hKh0f1mi5Lj0t6NUVa8pohoTJMGa4gGQ7heTXpqFt98vl/d1bYXFVmI7GI+aOc/nW8x8cQUWG0Ab6gw8SBkja2KSfWvFJ"
    "+8FNHgFp/ECu9R+r91exrtHoAEVdtLQI4ONw4BED98R3gooQ+GVkj8Zd2BpyAlYOxye9wf7RG6z6humx/rF2rH90Yz1jxsqWM79y"
    "rMCKB5M6VrNYXIutDS4qxfxhT2VtQLxbCi0dQbEWdlqCIRh4imb3GK5aG0guw8nXL8PJ2qmddFM7a6bGlrN1VGR7tm4s4k1mDrUS"
    "br4imy/uwGoH9eIXcJV80igfMyQBgkiqSvWdgiaxkbF2wxL76/9yRhWn3kW1el0ewjBXFiSA88IXGb4EgVeRLqFn05v7Mk3HD/Fl"
    "5MLRvoz6mTSLnrstNKiLkJgJKg0TeAyMBMd+d+K3x3Dj4JIGhgGEEIegeCs26ZnR7vZLi9HoEkhHXTC5bLJM1t9Gx3qDQiwhCoJh"
    "4FIdEi7ulwnONTbIBbSDcOklM8ry8+rGRo0PAx+HD21vQ+VYB2bGOp5F39n03UxCeqi39V4MAYBfExYMt5Zvk7fWpsOlZ0lHyA1L"
    "uGjYJR81/ZRO5egaZ3PdNo6sq6whldiwCQGRsYpG7kMrI1qfMgDaojfwMe0vXm5/Wf1uQOPbm3pR1yP4+Df/GMB4+bpfvMqhcGJF"
    "opGUeSaMbEQGXdjF5Ox3r0cLD6SZYF1egpkzyTv5XFSOEAi+mTCPBZVNKf8hNX8XBnmtI5G6wfgVl7MmxGLVrDX4ZN1DvAC6WqLj"
    "YJlg4XHn8SCg22V9AUNT9Qg0xtb/LFA/BHmF8A7y3UVAAhUj4a/g2O9PHMMLlZk1LhP8++RxF0bwWBIPqQAFx4+dONYkDznxUH7W"
    "sRoCtoJkR7BQNFpEMECNfIJy0k70MD4cfc/AtnYEqp3raD2vYamuTCZrXuBpEJmopOB1UGYm6nRhhHbulO5Fwm59hchKOHgPWAhZ"
    "VjdjiRJPJuSwb41p2+Im0+BIxiOvGiBpIII+Ql1PzDoUFR9Ht9diRurOt2a7UoAqTQuEPYX2SRaKG6P5PeQ+a2CU2hdGW+eKYF4C"
    "WW/ozDC4cJ30pLMfnZeLM19lfy4UDqbvo2eoBjKzvKVSug9dIBetAyKRs1cFTm50QK8Aui0959/cirM/2iHoItFmoGzkA6ORBX0A"
    "rLOzBcND/TNCX5j5Uiif8hMEMhE+ZBJMx6gDO3UhE1zK5LvaOztz2a6gPRt0o+VKa74tYFozn2mT2861dnQgyRLkspngzBX+R5r/"
    "1wXkHq8gTRecB/nbOluDb1q7gguZvF7uQldsaO34pvVKl/SMMoSu7gBp2EsI3LClM7h8SW5G0lixHyHJHnLSE282GVW/TpZdBwGd"
    "YUSwgVoRvChPu1RXgKWB/a3cmkYXCe5BrcQR3COhcLfICR6SS2Sg25vu6sHsKz6MLhOcZGoFhNONXrA2roZrVBHovVBB6NGyTLS9"
    "gNAqUrAUEaCPjZ3wh229UpxA/N/ElSB73+9pc+8zeoHgJ+tQIuzFeA+Cr7OvNKNOX0GyZLYjATgbinFoRPueh+t3iWp25l3yuA5D"
    "5XXVz+XOY4Hbu4LOXD4L5pHLHyFjfrmjLfv3SAhnMsISOfyTD7ra2/DxnDJJJp+/Ym5HijzXxlb0ei6XD8BiFwCN5CpYDhzamek8"
    "g6ttZERmrfGqxoBch/Rbe0dbIx5o7ZYRnclcaM+26StydjzdEHZ5tF1v6s5hvPncN+8Ep/KXuy8E7V2NwUdBW7uM+WI2902Qaedw"
    "3vG4L+xbgVLEVxg8Aj8v2GLh3Jt5KR/WwuMPHTxuYzAhyU2qeVxSUAMRTDIKCHk730Vsvnr/au0BTYEcwNtq7l0mRRmAvY4vxzPN"
    "nD9/lK++Nx/2DSBpT3Umyc2Eu26vcs72qhovvDgR5ZUc6L1+iDAstnsORtt1bKLiu9fj7Pz6gjbqyFnSIvzOBLSXkgUx+ochDy7D"
    "AoJqbRCtlh0NCwsQRd7fnTOFaaMo1dDgbzQxRVdKfG2bnZljkm64B4YOKAWBYmKOvqcwjXBvDxbHxa6ViWEGhsJrw8Fv30EgbkDS"
    "oHe0ZmIXsZN+DhlzQm6u5z6Mq66DAdNiOURzWnwpsKfpcHcTSsvBVio2mzFMLIltZJLH3S3lQhP+V/OuVFbILBcjJK4jFqB4zyZw"
    "0u9O/O4fbGjXB1ppjJHKNdbBWoo+k1hr9weNA1q3J36o6TgfgnGHJnShZdQS7C2YZ9UDcLFlH744b0CcBroLcLNZrYIQicyZxkdu"
    "ZiEU8w+oPYE1Qu5IOUMjJFhoT1e8PkjQXKsqmp2qIGDLHLIYvzJQYIk1o0ITOD2A8iYGBPZWPeWhWWP1dmB91MbSu0XqWaM6MldT"
    "viJXtRZTq1/2d+5FvRvqYCu6Yt4+psrJI8yzUuVPtVT5k6PKOcYXDo0r1KZh3yKwUJu5tVEFR0edfKA4mNVhKFV5KywMAeF4xpYA"
    "J3kZKmev7C+GJGq8N2uux4T+1iejW5vJ4A5WRlNOQJd6PdYlQpvXuWqaJlIvujb/r9Oj6rC5cJ3Er1sRfTgOEIp8aYBQPH7asvss"
    "ZTHRLZKEcIbe+KwSS+Pd9goLDFFJsFemZpWqZH1A2rRBA54BQ6QSucY1sbmSymZl4jULmYCzwNakIiIfUjNh36HZBdxio9qU6qT7"
    "mxJ0reao9d20nXT0FLbr7nR9reui1dqiYWgToP73ySaoprGJg0LPYarJu65qFGV+JmXKerrFOfz7Z/wfK/0zU9RF5VLIPcvHBadG"
    "233irmH6KGj+Myrpvvy2oR3+WsPFli74EBLeatUIHyfC8OaJOP7f2HAxc8Xyh3r2LSm6mXn7wQBacvgBKD4Vva0pda/QcFhqSkxu"
    "QGolBjRDoo5sXKiDW4WA+O/vAy2hUFsv/UJBRKOPoCDACq54RgP+yqYqSG6+3d+Aw91s7QTNfFuvXG7NtmTbz1/oTs7aeuagD+pQ"
    "PZ98nFCPlbTXIcnDHLeaP3EDFpl98JZbIvBSTIeqfg1ADrsGhj9sDsPG5jUl5Wo0lZ40hdJddeNh1NtHXXWN6jywVILqUjIpJ9WG"
    "xF0WW0YvHjDlRdckNSXuCcBD4vupyjQ32Goxn7hd9ObOZ/IxgU3K0fKTFRWfot94MY5UTtEWsSfSiXgn6SwK2AiG2+RgOYgmQqXJ"
    "FyXVyVaINCZAbQLt5J7rHVMzCtZkPA4O2/ZDJPhcoaab69e5dl9uJF/sJmpTGmZaR6yCyzryHi+3jKuaCmdpg10MsT6P1XC40mQE"
    "spgXpIq/o6tlE6myr0OW9Ely8E2O8VtV23tS78BrXQGIn7Qi7+HHREBGQ5+Dg76BpQbsL0kV9aygRSMzvE1rpnCz3UahC6UdqsBr"
    "mb+APimoIko0W1zoQI9KPpC+4NIuSXxthN0VN3UEWGjrZDDa4ceyZ/e4DUFALxXU1IZ0hAdQCeLvk1H+B7+F4xMaBkqTFR5yt8cU"
    "Hr+naRsT1D5jI8eaKFwoMkfi8aqrhaNHM71ruAcAz+5/cNSHGkG5IA2flM2o1yi8AOBoS4VgJmaxEOkJnMnnLmasgsy3CHo1Q48j"
    "NYdwhlGNbGjSFtebVZe620Pwl8xnURPdOmd8FiBfH4dROO2iqbBj2pqYh0enoo1uSsPazWEojfBFgkZ0Oh/edgWMuI3YC1hEyKUB"
    "BkVVWOfaSIPK1jpKdMbwBDc/IIO63OMKhXViCdoijpfpimUujps6EktFoLvvLYlMyDQwgllpQoWMKqxDQnjxN+MzySRNAUu9mJ0K"
    "jEiQlkaQblM/MqYq82TUb2de6cYlEumsbpSj/quW8XRTmB+Ct1SKDY9bAa6bZLUTpLOhcEM6+zVBNmu2Y1ph81VXRipfrLLyYvLO"
    "btLwSpxcRQq042A8qRJrMO/RwEZqUDkxglgyjX0gFZJT8LxM9SscBxAzuvkq3OtV2QN1sUlMVFNEd5evVAIjjYEaNX2T1oUY8usm"
    "EXunixLWpU7Lmfr0MUJ8FFUkoG+KwEeQjVtSjpfwzlEej8MRzNZtUeWKg8WJbD+kg6n6UsRd6xz+A4mAzS4dVzyLJjxhG88kjHRM"
    "i872zvaznn5K2hY/pkDtKg4nBuBKEzXQaMlgynwQ5n12P/rxsSmDhGfqlbXgCVfHnRq7v4B1Rp9UwIdOwQmYE+7a0To5dTPlhsl6"
    "d2qFiKZgAItUNViDrmKrl1IxGVWcYjIZztDqdV29FOgUUsBz+rz5gw//1YRWS3uVrftqxcJFDsxEAmXTk6lVhrcnG6O84gCzQ8rf"
    "/1kpL6M6FDFINNIFwraZ5g8+F/+nLm5LOgBWPiSRyi0xCPa/mhSlMKMlk8l6PfVTAuaGxszOKoX3KiXY4EoO0U1pWyJOjx1BoG3Y"
    "InEw0bJTWuTAcA6q3dWTEtivWAfbTgkKpDrCslM9F84A8Lr4mnxw/SgvzY1OEajKMKNIi8sRaluBt/ue6S5GmR6jHHvx7rbDBs1t"
    "U27cMHOI7GeTvihH22Lb6405MJ6W9pN6UWrIAI5yhashNorJcLnIEl0tD7AF3HKVHIv6KhOASNYo+vtVta4OvTGaVBriNiLubShI"
    "znpY+dcsIlQjNoQtog5yVmJ8Qyo+VKPw0BFIlgo8LblSNk+tqXUojuRTCbUZt0Z8BNgmeASHeR+ynSPhYCqS1spMjRpKReK0Fqyp"
    "e13V/GeNFOv2yhfrKqu4i3v+pkYNw/SLNrcP6S5AvwbOn6sHiRIQ2gLkJCi2zo4rxuMS1RTbuQIin0HVwFCG76/CtyQiLq5qYEkd"
    "QuswaBiFDDFLznClJG7Iphg+bWg89++tCsIl/ONjfGdpYz1NGLclakkiX+IJo2NSRDcSyZ1up43u4ZCyP30TF6hmPrZy9rDpvHUh"
    "as2MHMtpYRaZbRmO2qAJOrhEUbInzlpvE/Xo35mq9vn2CLjtG9QEeHbmk8GzgRuEPWMi0zIKqPvqesl3JMDwDk5LZNTcb+FTwccF"
    "ptIq9kFsnCqdhVdDVl3f1tuAMFkmurRrjneA7hAg73CZLPljU7q5IdtowcvC6SlamHIlR4hUpYlWEVHKvcohhnuNSSf4NRsmNp8y"
    "ro0gvS1DUpCnST0NYLjtHD7Khr3YW9VHmeGU+L7yUu3thF7rkyTL+iRENvp+TzYucYxgIEMN0VlQClwWUaCqHVIT11h6igGUJ2Lm"
    "/hXBZ2FnZXe1JHjW+UVacCunLbyI+q+nhpLyYerwovGT8bz1gdUxBb5jjMIvYVDt7NxofdJ5p0nnWC+S8IL7bdyUKRUf8es6+SNO"
    "BDLSxUm6nTRZ1KVQ2RV1cWmSDpjiT6RTzZ1JBqlBHjDQ6NVsO3Q2SIxvDCO/OPXp57pZHJ7Pcf6XtlM3Q8R21FUEeyW0p138X3Gc"
    "Szk1HB4APqLw+P9hpa9+4JkPgiIEcvNpu7b/uXWzbgfLe6YoTFzOBOGbYsI3xYRvqiG8Cx/GxXE1mSx3yX2Q9yXq58wBD3UX6m2r"
    "LP4LJGT/++YylUdY/SlWjXbBD8HWH3D/NY2kuDNKTJx/esa4c5LR+vTkn78w21C1PB2fL13IS/odmWi/Iqgx/vpv/hcyj8bzTbyN"
    "R3voyRB6mI/JydVxjrE3GLU85kAgoLP1RbeVwiSrISa9Y9rOGwRzqufzTR7nJJA3b/bYlCrVZir125WckA8S0EctbHjDyfhP1ZuM"
    "bmGhXkd4XGEx06tD5jwUnKokhlqTQCbEt8uzJPCIYMAb2hX19q1ESDF2/I8mAWjwWfOnn30sm4zF46k5iaDuAQTxuQON5jEqGE1Y"
    "2iMNGhtov5I7+1jyuWsKe9xBPxNTUJReMk528fa6HWBqiXSnl3MbtA8efrXNSj9kN0hGQG1JbaC/X17OUzPlUIopq6t9JXatmyZq"
    "OXbJD+u99TYk6o06CH/PGtMHw6v+mDjKF/PqQrjAuYlvQuXLEWCgAPNPN1f8PWs2DOxTQOUfGodMsv0whXHRK+2dg/WiFoisq8so"
    "l5pkMTfk+2rZsQ6oQ+Be3DTcA6op5BzdlJoB6hOG0tYGFIH5aJrC4rsUCTomLsh9+Cyyv2I+eJe0oKrOHiJp9L/iM5+VwIKUU3BC"
    "XJ7M1yinzJ7NHOGnx3tAtdCzOKEpP7cU8U5Se4ZKyovylkJtvhaIJs4OY7BhWF1NU1VFU1RwgRIFfRLDeX262N+4qmLq0V8XFw4Y"
    "34BcgDynmg83g+llw7vo9Wsj7swc50BoospCGW7l40keg36OG0jVHoo147LcNvkbTb2qLKI4r0QEKcKg6kwLUuL9thsGjMuJWlrb"
    "jPdSFL/zTYHPPHGr8AlO9KkrgUys0XjxALPkQiHmJTGkEbZLB8nVwzAQoCGw8zxYY0kk26IPsSLGxhhd92wsPdRYupEC3RyQ1CPa"
    "1KhhNJau3EWIsVh3HnIrCGL2EcpggLCiRDWCEX+4fLgdITXjdFOlM7Y3WlamQTcUKNy5N0UUoGdjSLvXE3VucVXVBIIswEBaGa11"
    "Qsgh6eGCymRkV4R9NR9AN1MIIUkXxVgp4pMieEGCHPzeGJsKtUn1iCHjmFc2MSGIJRO2Fb9usDKmpz8M6QdIK9Wb1a+K8tQ4evOV"
    "K3QtVQPub9/Xc7BMakQ0ne5kYoBgtxzdeWXyMujPekJiGS9nbXw0pQrMYXJaaGWdfz19hQewyE5OtT8oySa4+DI2NtnLHR2nG9NK"
    "07b60sDj2E7YyBAvOj4jCD+hZR1128V/4hVdCpSNZ9pOAL6fPvRElEMPQrEPxOZdvyeLL62ht/t8jKE36RnoqIStH0bpEvC+l/Z0"
    "O+xoSzfH9l9OAymTEwQQaxLQqz9aJacWV6OhPmT+FNpW+pbB31VJLyc7pjEDUpDXGy6buI/VEYhRpIbqL2kwL4EXWMyXOOJGivs0"
    "7OPvpuFOCG9Tsn0rudpH25Jg0WR4ZX5NOVxzPZyLKbKVfe2oJZndxUYk3qmlZf1XNT+WnJcpRBD4g0m7jXzGWqhou8JeGz/RO43e"
    "uffC6AFvy4N5fG8BNxsBNwccJY7UsmceETHhX4nG1aODgzGpMJ+RQzkX1Fk2LgZOqFsfTs4UxpkaXry12AvQQwSkP7IX0dWwYRBN"
    "ONyei57c1/ZfiWA0zMIPknvWD+A/ohDZsBZjmhQcSSfKa8gx0kM1o9E+uLxifTTovL+NCnmU680rZDGejT2Tle5tolLA0fZtcuU+"
    "mzJILiICsTDBRYcoPNQBqxPSUDg7KDybtIPahGuS8OSHpZ3DKGQTrzUUAjLBNoInCwdysC1pgLSRpDUdN2tAOyUAivyTFCKixEmO"
    "sitJySdbEW5rMRQhoctDb23p6UsCaVDUyiAljkCSo88g42oiGEmGuCJCJMxlnBCxWC7FoPLkrKifTE85twntB433iOEBgfBEM4zt"
    "FpURWIWEcsvFZbH7y9buqn+aML3WZdWz5vSDRkq5GlKaooeh+v6tv73ssOWyBTPp5SKUBR7VXI/6M7cGlXVceYz27xfJmOBiTRmM"
    "LqlDdBge1z9dUbNlCmmKEzUicUTlDHnbVM5Qc7jF9WfPLstFnsucqK5JMOdw6WB1KuwzKEVXW2GBfyleprcCjJ7uM4DRgWviHxyv"
    "ObatlzRuKDBpMVx/Ho5NcSrrz2uMIMl4Z0kA43PNW6gdNGOnZ0s7jeOcedQajNXk+pEAz8GEGoDnaxdiVHv+GQcp7E1nVPb4Qf9X"
    "izuxhyoWU22sOjdYT9llP1Qj19wniUSWHLNJ+yUZRlXTxvtPQjifRRx4wxo75Cbhu3rITQy+BWjW8NWHcQazaRxfoJlViQ6DNXpS"
    "au+OcyR14JwtB03juXeJ5zDFUwjHf/SXTz/5VxOg8/3hOqdKImVc3RgxLOGN3AM6vMEhePdIDYKvOdf2Df3GNy+frvWDKYDWOtlN"
    "9p7BrXPQ3+vnm7yzIQUTvOMlvEwdZ52sCFXzzWTG2xexuc3XWlHgzLIhmmeZE3NXU3rU3FOlSImTnJytTt0TT1ztZ21RsCk50qMs"
    "zNFMM/8f7KajsJ7xVNdeJgikxusoArkgvW8vU2Y1dTX+nNZ8KcNnTEuNvYtZx7N35tDSGhvG8ol0AehWra0Spfd582effn6q5eSn"
    "f4FakGlnMm0t3bkWPaWa45RCQPBluLAq6UOEzZ+x+l/OYFR2Q3CQeYwxHAi4qM4RjwPvyHAfU8NPczfkkDI5LvCXlz3mhFIUjAC1"
    "Lay68/vxlL60O8M6PE0bo2zH7xZasCuX1bo0pFwOCgOuzizgeYY3n/IEcLMb4n05QFKC5Lv3XDjHnL1VXESy1eYtLB1ULUqurD3T"
    "0dYV78zRA9K9I6mSZOBRCZe6NUi1BtKLkMgRMyItU9GWgqfHPKjktLDL1xnFwXLttCs//IYbvL33AEqPT3jd62F73B5vz47FcnNn"
    "znJZPzuTEL+NaypWOXvF5F1N1tTEBb7z3p7z3t3/HJtwKwOosuMEOy93dLfHSQwzHCgnJCTLe44gKlZq61MbPuTk7zEuUvro33is"
    "6LjFjvfQzv3Mb53XxPtY6r7Oq237jknqC+gIm2TkuH6tYlTe48Zm/6hUfjeHnrpTTpngEfXxn8o0cQ2x3QTKdPxDrc7wXuMaDDy0"
    "Xw9nldTZJ169ssc1gkk6Wrv9/e2K0szWZS8Rin+9AcWxAeE1OQ3xmm6Re82w7L4XW4PPeJUjkOn29H8IXzMtg7NGXvUwg2hz3szK"
    "udWVKlfdEq+0RukHY8PrM2/B6l/6VcVxwK7eMFL15IeOKbFxKDE8fwfDr5CNpuP8bvcGTYhl+UvzF1988E/N3u7GzhY59MSLB/Jg"
    "Lig8SSSaaoFYNHkEWq7THBYiRNOSYVsaJsXcSFQZmZyPS7g7wbQgQ9vRb4qHQ2wd1zq+7sVx7d6ahg/f4pw/b6xNNVRp8sZqh+ZX"
    "JL2WMoqPVdwQ7OBZlrt69F780hoKNdWhUDy2wwWvMVHgcfrQwf3tcq7mjE89UBfAUMHgt3LPd1rFpZULrgIWIcjeZ6o8pBw5i6N3"
    "tAc167pXBb1pPR/Ar62FNush9TsodTK/LUPm/uBPp5o/b0mb/ovtHR3C8v4ECIwRRS7cslmxxBVJq+ixjfYXlWpvcfvADpBLQMWG"
    "YDstH1Vwc0aAuTEa+GSMQs1AtN0cnYyZFcqORqh5YQrEVkTGBqV2NtLugnis2cO5qdsmrMcqCe8gkNge1fSjS0hPTG4gJt28jz+l"
    "Q6zvamchn6UGVcRHsvyuZM6gNLtvQTRK/nJHxljXc2IMznTl8qh/e7fRfGxBYfqFy52t5icCvkscN5QcM2HyRlkzfe7UWhcyO+K8"
    "2XqLq6FDJ/rukNi4KA4/mmWK4hrt6M1Am44apdnti9FIoZke0M2Fl2PI0Rj13bdxrfT6Sr2TVqq5QLjxgLwaX1y1FD5tyzOSuVSN"
    "lSp0x5+fdyzORFrfrZkJm0o24RtDlBkbInUHd+PwKq62ZGR4LyoT2s1Rqi5xS+smR30FzqFL5b1NBnNZ0zNO/jCQWh5EFASz+ltL"
    "d6ajI4Zrhwm6sywmajw+KTWnr4rm1Nfa0qhAF15/0aR2KaA45Ke6ME0osZrrJqpmE5tvowwCXW5Xt8WRyP6K12qAwJf/wIl+7RyP"
    "0AHaHijRAy07YJDNLsKbaAESXRx+PTWCZzhh54voauP5YhORjQjIGhqcw/yb4tiazrU9oF7haWfJbXFpFFw7q+T9Rt/ClM9ua8rL"
    "VVxpDcphU6zthyIzv5Ta9yDlNCyodT9B5Uygn3ATTSh2ssV3hpP8lLCk6eEgawZEINwcKDIxKblaDRujD63Ufr1G1Qoz1dsazHEm"
    "xj+dBJpB992ZYK4VH233dgsYTc8oJvGHSSi/e/qokSBVC7dZdGfJFM+JA+ISq4yrPTQ/IKUs7op6WS3444u0PowDwsnMtESpY7BF"
    "FhWpc/kDraQxm0J4oLgJv7hNzjWxat3mrhuQoJ/l6fmUCmsSJvjgCy20NGoNn+jleKV4iQisVmuZUBrC5J6qMT7Ptw2m4F5iSSPu"
    "h310m0hcdM1z4jRyzNC3WrHj4J3a2gc/5qIBt5rCFXKfjdn6+Vqn9W1exT2kCZ3qZo+F/PaAIvu7Vlreo6DTOhUZuFr48TPZE5RP"
    "1ZXAi9KEczSwST6uNwpTA6d7d66N6D4dd3Y8EFdpT5/Tn3rRw1awvH6VKqORxBjrFoE2Ooq7zDdBA2DP2rjuq7MRS6ZtNFORWAkR"
    "CxPEx1l9mcPXw3Qh6Ta/KvDoKjZJ8ughFzRdwhD2BzCGYzghOsScDKzFCnZbGoiqm6psCYdvvv2LspYUasvgjptd6igRNv0V3OwF"
    "IibNyC0Qd4lhXbtaLrf1K0mqOnF1Bf7KkPoaqaiKc3I2/uoitaoqwDZSnhaHY805AXJUkKY9mcAolDlDW9uRSmDap80Bf+Lk+Zz1"
    "xlLR1JCaGedk+V01JIpm+NtJYxO6t8+efewXGGhsWivCvV3ViS13jPTbUDUxm901pRHtQ3lcY/N1eTwmx8MeZT+X8nVFM/WOT4i1"
    "FCyGFAa4ajEO78Gm1ptpcS5p23+PJe+2asStJy+xoHyTlRe7Y2o6qJ9PfvBF8xexW5klN5sfVvq6/Wx3e6f51Qw/nMgaKheMMO51"
    "VhkfmNUhV9OXHGzu+nKxtA5ntRM9MA3ZSESZl0TN7U39cTaEcFQgmFfSonH764suNG83nfsnZfu/WWs2gcsJ6FJRPqLwPPWbcNwE"
    "U54mPhi8q8UxFAZ7XLogFS4GtiCJFU39mCRukV8d1THb/QakckvzJx9aQjfoD9kG8bktUqkZaHODUtC/qTScvkl+PpWPD5gN7Uok"
    "s195xuAGILbFuYPpx/iJGvebwWQN/OwKU+urnObiHKoRjZkwP7UrZzkoA01J/Zb7pTWZv5b549Bx/sT0gPwQKU7MMT9NVwLBfedG"
    "f7XZeelSjkJu0m2t3+8hJsjE0c0V9wuiokkKusnZwE5JkLhtAJIyGVGkpPDEJEhOfXRSmJn7NuQnakDKf3OfYB7w+UrmjIAChXMM"
    "uzLC/n8B4TcXtg=="
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

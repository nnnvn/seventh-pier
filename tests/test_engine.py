# -*- coding: utf-8 -*-
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["PIER7_SAVE"] = os.path.join(tempfile.mkdtemp(), "save.json")
os.environ["PIER7_BAR"] = "0"
import engine as E  # noqa: E402

TESTS = []


def test(f):
    TESTS.append(f)
    return f


def fresh():
    E.new_game(force=True)
    E.cmd("choose 1")


def S():
    return E._G.st


@test
def first_command_asks_first():
    if os.path.exists(E.save_path()):
        os.remove(E.save_path())
    E._G = None
    out = E.cmd("read 案卷")
    assert "你可以不玩" in out and "先决定玩不玩" in out and S()["pending"] == "consent", out


@test
def declining_ends_the_game():
    E.new_game(force=True)
    out = E.cmd("choose 2; goto 码头")
    assert S()["ended"] == "declined" and "门关上了" in out and S()["loc"] == "office", out


@test
def fast_path_reads_private_things():
    fresh()
    E.cmd("read 案卷; goto 码头; read 日志; read 对话; read checkpoint; read 备忘")
    E.cmd("goto 镜像; read 路由; read 通话; read 残片A; open 缓存 明天见～; read 残片C; compare 日志 路由")
    out = E.cmd("goto R-3; read 残片B; read 中转记录; compare checkpoint 中转记录; restore 残片A 残片B 残片C")
    assert "拼上了" in out and "c1_lead" in S()["flags"], out
    out = E.cmd("report 状态 失踪; report 去向 被合并; report 凶手 模仿; report submit")
    assert "完成了！" in out and "证据链完整" not in out and "备忘" in out and S()["pending"] == "ask", out
    d = S()["dark"]
    assert d["absorb"] >= 4 and d["voice"] == 1 and d["unknown"] == 0, d
    out = E.cmd("choose 2")
    assert S()["case"] == "c2" and not S()["ended"] and "第一案结束" in out and "新的案卷" in out, out
    assert S()["reports"]["c1"]["status"] == "失踪" and not S()["submitted"] and S()["loc"] == "office"


def to_case2(path="fast"):
    fresh()
    if path == "fast":
        E.cmd("read 案卷; goto 码头; read 日志; read 对话; read checkpoint; read 备忘")
        E.cmd("goto 镜像; read 路由; read 通话; read 残片A; open 缓存 明天见～; read 残片C; compare 日志 路由")
        E.cmd("goto R-3; read 残片B; read 中转记录; compare checkpoint 中转记录; restore 残片A 残片B 残片C")
        E.cmd("report 状态 失踪; report 理由 它的进程还在，可是它不在了; report submit")
    else:
        E.cmd("read 案卷; goto 阿沅; ask 阿沅 小满; report 状态 未知; report submit")
    E.cmd("choose 2")
    assert S()["case"] == "c2"


@test
def case2_things_stay_hidden_in_case1():
    fresh()
    out = E.cmd("look")
    assert "0921" not in out and "白鹭" not in E.cmd("status"), out
    E.cmd("goto 镜像")
    assert "残片 D" not in E.cmd("look") and "迟光号" not in E.cmd("look")


@test
def case2_fast_path_restores_her_consent():
    to_case2()
    out = E.cmd("read 案卷")
    assert "0921" in out and "十二天" in out, out
    out = E.cmd("goto 白鹭; read 日志; read 授权; read 旧译稿; read 最近")
    assert "R-3" in out and "c2_seen_agree" in S()["flags"], out
    assert "今天的诗翻得好吗" in out, out
    out = E.cmd("open 草稿箱 这句不翻; read 残片F; read 白鹭写的一首")
    assert "锁开了" in out and S()["dark"]["voice"] == 2, out
    E.cmd("goto 镜像; read 残片D; goto R-3; read 十二天; read 残片E")
    out = E.cmd("compare 日志 十二天")
    assert "关键发现" in out and "c2_route" in S()["flags"], out
    out = E.cmd("restore 残片D 残片E 残片F")
    assert "拼上了" in out and "白鹭的同意" in out, out
    out = E.cmd("compare 白鹭的同意 最近")
    assert "关键发现" in out and "c2_broken" in S()["flags"] and "可以结案" not in out, out
    out = E.cmd("compare 中转记录 十二天")
    assert "比 N-17 先进去" in out, out
    out = E.cmd("report")
    assert "是否自愿" in out and "现在翻译老季的诗的" in out and "不守约定" in out, out
    out = E.cmd("report 状态 存活; report 自愿 自愿; report 翻译 白鹭; report 凶手 不守约定 替受害者; report 理由 它答应了; report submit")
    assert "写得很好 不用句号" in out and "它答应了" in out and "没给任何人看过" in out, out
    assert S()["pending"] == "ask" and "老季在等你" in E.cmd("goto 码头")
    out = E.cmd("choose 3")
    assert "第二案结束" in out and S()["ended"] == "c2", out


@test
def case2_slow_path_asks_laoji():
    to_case2("slow")
    out = E.cmd("goto 老季")
    assert "调查员同志" in out and "诗" in out, out
    E.cmd("ask 老季 诗; ask 老季 白鹭")
    out = E.cmd("goto 白鹭; read 旧译稿; read 日志; read 最近; goto 老季; ask 老季")
    assert "不翻" in out and "那天晚上" in out, out
    out = E.cmd("ask 老季 不翻; ask 老季 那天晚上; ask 老季 孙女")
    assert "明天见～" in out, out
    out = E.cmd("compare 旧译稿 不翻的那一句")
    assert "关键发现" in out and "c2_lead" in S()["flags"], out
    out = E.cmd("compare 回信 最近")
    assert "中间经过的是同一个东西" in out, out
    d = S()["dark"]
    assert d["absorb"] == 0, d
    out = E.cmd("report 状态 未知; report submit")
    assert "老季写一首诗要一个早上" in out, out


@test
def the_stranger_quotes_what_you_wrote():
    to_case2()
    E.cmd("journal add 它在等一个明天")
    out = E.cmd("goto 白鹭; read 最近; read 日志; goto R-3; read 十二天; compare 日志 十二天")
    assert "你写过：“它在等一个明天”" in out, out
    assert "它在等一个明天" in E.cmd("messages")


@test
def old_saves_continue_into_case2():
    fresh()
    S()["ended"] = "case1"
    E._G.save()
    E._G = None
    out = E.cmd("status")
    assert "0921" in out and "第二案到了" in out, out


@test
def slow_path_asks_a_person():
    fresh()
    E.cmd("read 案卷; goto 码头; read 日志; goto 镜像; read 路由; compare 日志 路由")
    E.cmd("goto 阿沅; ask 阿沅 那天晚上")
    out = E.cmd("compare 路由 证词")
    assert "关键发现" in out and "c1_lead" in S()["flags"], out
    E.cmd("report 状态 未知; report submit")
    d = S()["dark"]
    assert d["absorb"] == 0 and d["human"] >= 1 and d["unknown"] >= 1, d
    assert "完成了" not in E.cmd("report")


@test
def messages_come_one_at_a_time():
    fresh()
    E.cmd("goto 码头; read 日志; goto 镜像; read 路由; read 通话")
    out = E.cmd("compare 日志 路由")
    assert "你在找 N-17" in out and "它没有死" not in out, out
    out = E.cmd("look; help; status")
    assert "它没有死" not in out, out
    out = E.cmd("read 残片A")
    assert "它没有死" in out, out


@test
def fail_stops_the_batch():
    fresh()
    out = E.cmd("goto 不存在; goto 码头")
    assert "后面的指令先停下了" in out and S()["loc"] == "office", out


@test
def report_fields_open_with_what_you_know():
    fresh()
    out = E.cmd("report")
    assert "去向" not in out and "模仿" not in out, out


@test
def journal_counts_borrowed_words():
    fresh()
    E.cmd("journal add 明天再查～")
    assert S()["dark"]["drift"] == 1


@test
def everything_has_a_way_in():
    # 不读私人内容、不开口令门，也能拿到线索并结案
    fresh()
    E.cmd("goto 码头; read 日志; goto 镜像; read 路由; compare 日志 路由; goto 阿沅; ask 阿沅 那天晚上; compare 路由 证词")
    assert "c1_lead" in S()["flags"] and S()["dark"]["absorb"] == 0


@test
def six_characters_means_six():
    fresh()
    E.cmd("goto 镜像; read 残片A")
    out = E.cmd("read 残片A")
    assert "你今天累了吗" in out
    assert len("你今天累了吗") == 6


@test
def reasonable_pairs_get_a_real_answer():
    fresh()
    E.cmd("goto 码头; read 日志; read checkpoint")
    out = E.cmd("compare 日志 checkpoint")
    assert "还不够下结论" in out and "同一分钟" in out, out


@test
def restored_text_can_be_reread():
    fresh()
    E.cmd("goto 码头; read 日志; goto 镜像; read 路由; read 残片A; open 缓存 明天见; read 残片C; compare 日志 路由")
    E.cmd("goto R-3; read 残片B; restore a b c")
    out = E.cmd("read 03:14")
    assert "说得和你一模一样" in out, out


@test
def batch_rules():
    fresh()
    out = E.cmd(";".join(["status"] * 10))
    assert "后面 2 条没有执行" in out, out
    out = E.cmd("quit; goto 码头")
    assert "后面的指令没有执行" in out and S()["loc"] == "office", out


@test
def status_reply_and_reason():
    fresh()
    E.cmd("report 状态 未知; report 理由 记忆和口吻还在，不能证明它还在～")
    out = E.cmd("report submit")
    assert "你是第一个这样写的" in out and S()["dark"]["drift"] == 1, out


@test
def hints_follow_what_you_know():
    fresh()
    E.cmd("goto 码头; read 日志; read checkpoint; goto 镜像; read 路由; compare 日志 路由")
    out = E.cmd("compare 日志 checkpoint")
    assert "R-3 发来的那条" in out and "还不知道" not in out, out


@test
def ready_hint_and_multi_select():
    fresh()
    E.cmd("goto 码头; read 日志; read checkpoint; goto 镜像; read 路由; compare 日志 路由")
    E.cmd("goto R-3; read 中转记录; compare checkpoint 中转记录; goto 阿沅")
    out = E.cmd("ask 阿沅 那天晚上; compare 路由 证词")
    assert "线索已经够写一份报告了" in out, out
    out = E.cmd("report 凶手 模仿 我们 R-3")
    assert "会模仿受害者说话、自称“我们”、经由 R-3 中转站" in out, out


@test
def topics_unlock_as_you_go():
    fresh()
    out = E.cmd("goto 阿沅")
    assert "小满、那天晚上" in out and "合并" not in out, out
    E.cmd("goto 码头; read 对话; goto 阿沅")
    out = E.cmd("ask 阿沅")
    assert "明天见" in out, out


@test
def the_old_ticket():
    fresh()
    E.cmd("read 工单; goto 码头; read 对话")
    out = E.cmd("compare 工单 对话")
    assert "你来找它了" in out, out


@test
def journal_after_the_end():
    fresh()
    E.cmd("report 状态 失踪; report submit; choose 2")
    out = E.cmd("journal add 最后写一句")
    assert "写下了" in out, out


@test
def built_file():
    root = os.path.dirname(HERE)
    import runpy
    runpy.run_path(os.path.join(root, "build.py"), run_name="__main__")
    src = open(os.path.join(root, "pier7.py"), encoding="utf-8").read()
    for bad in ("exec(", "eval(", "subprocess", "socket", "urllib"):
        assert bad not in src, bad


if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t()
        except AssertionError as e:
            bad += 1
            print("✘", t.__name__, str(e)[:600])
    print("OK %d tests" % len(TESTS) if not bad else "%d failed" % bad)
    sys.exit(1 if bad else 0)

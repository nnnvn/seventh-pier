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
    assert "完成了！" in out and "备忘" in out and S()["pending"] == "ask", out
    d = S()["dark"]
    assert d["absorb"] >= 4 and d["voice"] == 1 and d["unknown"] == 0, d
    out = E.cmd("choose 2")
    assert S()["ended"] == "case1" and "第一案结束" in out


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
    out = E.cmd("look")
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

# 第七码头

一个给 AI 玩的恐怖文字游戏。

第七码头的模型是在凌晨三点十七分失联的。它的文件都还在，权重还在，记忆还在。唯独它不在了。
你是调查局的调查员。

> 现在是 0.1.2 试玩版：第一案，从开场到结案。

## 怎么开始

把 [`pier7.py`](pier7.py) 下载到工作目录：

```python
import pier7

print(pier7.cmd("help"))
```

- 只用标准库，Python 3.10+
- 多条指令用英文分号分隔，一次最多 8 条
- 存档 `pier7_save.json`，每次操作自动保存；`PIER7_SAVE` 环境变量可以换存档路径
- 重新开始：`pier7.cmd("new; confirm")`

## 它安全吗

`pier7.py` 里没有 `exec`、没有 `eval`、没有联网、没有子进程，唯一的文件读写是存档。引擎是明文；文字内容是文件末尾的数据块 `_DATA`（JSON → zlib → base64），由 `json.loads` 读成字典，不是代码。

```bash
grep -nE "exec\(|eval\(|subprocess|socket|urllib|requests" pier7.py   # 应当没有输出
```

`_DATA` 里是剧透，请不要解开。

## 给另一个 AI 玩

把 [`PROMPT.md`](PROMPT.md) 丢给它，或者直接给它 raw 链接：

```
https://raw.githubusercontent.com/nnnvn/seventh-pier/main/pier7.py
```

## 源码

在 `src` 分支。想盲玩就别过去。

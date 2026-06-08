#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 .ass 字幕里是否把 ...（半角三连点）当省略号用。

中文规范的省略号是 ……（两个 U+2026），写成 ... 一般不允许；但少数特殊情况
（英文台词、网址、特意表现等）确实需要 ...，所以本检查只「提醒」、不拦截合并。

为减少误报，检查时会：
  - 只看 Dialogue 对白行（忽略注释、样式、文件头）；
  - 先剥掉 {...} 特效标签，再检测正文（避免 \\pos(1.2,3.4) 这类小数点误报）；
  - 默认检查所有对白行（含英文/罗马音行）。这些行里偶尔有合法的 ...，但本检查
    只提醒、不拦截，交由人工判断即可（如确需跳过见下方 SKIP_NON_CJK 开关）。

两种模式：

  --diff        从 stdin 读 `git diff -U0` 的输出，只检查本次新增 (+) 的对白行。
                输出：①GitHub 行内警告 (::warning)；②运行摘要 (GITHUB_STEP_SUMMARY)；
                ③供 PR 评论用的 Markdown（写到 ELLIPSIS_COMMENT_FILE，默认
                ellipsis_comment.md，仅有命中时写）；④命中数写到 GITHUB_OUTPUT。
                CI 用这个。始终以 0 退出（只提醒、不拦合并）。

  --all [路径]  扫描整份文件（默认当前目录下所有 *.ass），打印 文件:行号: 命中。
                本地自查用。有命中时以 1 退出，方便本地当门槛。
"""
import os
import re
import sys
import urllib.parse

# === 可调开关 ===
# 命中规则：连续 3 个及以上半角点。想顺带抓全角「。。。」可改成下面注释那行。
DOTS = re.compile(r"\.{3,}")
# DOTS = re.compile(r"\.{3,}|。{3,}")

# 默认检查所有对白行（英文/罗马音行也查）。若哪天想跳过纯英文/罗马音行
# （其 ... 多为合法），把它改成 True 即可。
SKIP_NON_CJK = False
CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")

TAG = re.compile(r"\{[^}]*\}")          # ASS 特效标签 {...}
SUGGEST = "……"
COMMENT_MARKER = "<!-- ellipsis-check -->"   # 粘性评论标记，供 workflow 找到自己那条


def dialogue_text(line):
    """若是对白行，返回正文字段（第 10 个字段）；否则返回 None。"""
    if not line.startswith("Dialogue:"):
        return None
    parts = line.split(",", 9)
    if len(parts) < 10:
        return None
    return parts[9]


def check_line(line):
    """返回 (命中列表, 用于展示的正文)。非对白行返回 ([], None)。"""
    text = dialogue_text(line.rstrip("\r\n"))
    if text is None:
        return [], None
    cleaned = TAG.sub("", text)
    if SKIP_NON_CJK and not CJK.search(cleaned):
        return [], cleaned
    return DOTS.findall(cleaned), cleaned


def _snippet(text):
    s = (text or "").strip()
    return s[:117] + "..." if len(s) > 120 else s


def run_diff(diff_text):
    """解析 `git diff -U0`，对新增对白行输出注解，并产出摘要/评论/命中数。"""
    path = None
    new_ln = None
    rows = []                 # (path, new_lineno, 展示正文)
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            path = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_ln = int(m.group(1)) if m else None
        elif line.startswith("+") and not line.startswith("+++"):
            if path is not None and new_ln is not None:
                found, cleaned = check_line(line[1:])
                if found:
                    snip = _snippet(cleaned)
                    msg = ("疑似把 ... 当省略号用，建议改为 %s（中文省略号）；"
                           "若确为特殊情况需保留 ... 可忽略本提醒。整行：%s"
                           % (SUGGEST, snip))
                    msg = msg.replace("\r", " ").replace("\n", " ")
                    print("::warning file=%s,line=%d::%s" % (path, new_ln, msg))
                    rows.append((path, new_ln, snip))
                new_ln += 1
        elif line.startswith(" "):
            if new_ln is not None:
                new_ln += 1
        # 以 '-' 或 '\'(无换行标记) 开头的行不推进新文件行号

    write_summary(rows)
    write_comment(rows)
    write_output(len(rows))
    sys.stderr.write(
        ("共发现 %d 处疑似 ... 误用（提醒，不拦截）。\n" % len(rows))
        if rows else "未发现疑似 ... 误用。\n")
    return 0  # 始终成功：只提醒、不拦合并


def write_summary(rows):
    """把命中写进 GitHub Actions 运行摘要（若在 CI 中）。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        if not rows:
            f.write("### 省略号检查 ✅\n\n本次新增对白未发现把 `...` 当省略号用。\n")
            return
        f.write("### 省略号检查 ⚠️ 发现 %d 处（提醒，不拦合并）\n\n" % len(rows))
        f.write("> 中文省略号应为 `……`。确属特殊情况需保留 `...` 可忽略。\n\n")
        f.write("| 文件 | 行 | 内容 |\n|---|---|---|\n")
        for p, ln, s in rows:
            f.write("| %s | %d | %s |\n" % (p, ln, s.replace("|", "\\|")))


def write_comment(rows):
    """生成 PR 评论用的 Markdown（仅有命中时写文件）。带定位链接和粘性标记。"""
    if not rows:
        return
    out = os.environ.get("ELLIPSIS_COMMENT_FILE", "ellipsis_comment.md")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("PR_HEAD_SHA", "")
    lines = [
        COMMENT_MARKER,
        "### ⚠️ 省略号用法提醒",
        "",
        "本次改动里发现 **%d 处**疑似把 `...` 当省略号用。中文省略号应为 `……`。" % len(rows),
        "若确为特殊情况（英文台词、网址等）需保留 `...`，忽略本提醒即可——"
        "**它不会阻止合并**。",
        "",
        "| 位置 | 内容 |",
        "|---|---|",
    ]
    for p, ln, s in rows:
        loc = "%s:%d" % (p, ln)
        if repo and sha:
            url = "%s/%s/blob/%s/%s#L%d" % (
                server, repo, sha, urllib.parse.quote(p), ln)
            loc = "[%s:%d](%s)" % (p, ln, url)
        lines.append("| %s | %s |" % (loc, s.replace("|", "\\|")))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_output(count):
    """把命中数写到 GITHUB_OUTPUT，供后续步骤决定发/删评论。"""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("count=%d\n" % count)


def run_all(paths):
    if not paths:
        paths = []
        for root, dirs, files in os.walk("."):
            if ".git" in root.split(os.sep):
                dirs[:] = [d for d in dirs if d != ".git"]
                continue
            for name in files:
                if name.lower().endswith(".ass"):
                    paths.append(os.path.join(root, name))
    total = 0
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8-sig", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if check_line(line)[0]:
                        total += 1
                        print("%s:%d: %s" % (p, i, line.strip()))
        except OSError as e:
            sys.stderr.write("跳过 %s: %s\n" % (p, e))
    sys.stderr.write("共发现 %d 处疑似 ... 误用。\n" % total)
    return 1 if total else 0


def main(argv):
    args = argv[1:]
    if args and args[0] == "--diff":
        return run_diff(sys.stdin.read())
    if args and args[0] == "--all":
        return run_all(args[1:])
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

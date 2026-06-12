#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 .ass 字幕里的 [Aegisub Project Garbage] 段。

Aegisub 会把本机的工程状态写进字幕文件这一段里:音频/视频文件的本地路径、
滚动/光标位置、缩放比例等等。这些对发布出去的字幕毫无用处,而且会泄露本地
盘符、目录结构和原始文件名(隐私)。本脚本把这一段整段删掉,其余内容(包括
UTF-8 BOM、CRLF/LF 换行)原封不动。

三种模式:

  (默认) [路径...]   就地改写给定文件;不给路径时,清理当前目录下所有 *.ass。
                     GitHub Action 用的就是这个模式;也可用于一次性清理存量文件。

  --check [路径...]  只检查不改写。若仍有文件包含该段,打印它们并以非 0 退出。
                     可用于 CI 卡点。

  --filter           从 stdin 读入单个文件内容,把清理后的内容写到 stdout。
                     这是为 git "clean" filter 预留的(本仓库当前没用,留着备用)。
"""
import os
import sys

SECTION = "[Aegisub Project Garbage]"
BOM = b"\xef\xbb\xbf"


def strip_text(text):
    """删除文本里所有 [Aegisub Project Garbage] 段。

    从该段标题行开始,删到下一个段标题(以 '[' 开头的行)之前为止 —— 这样
    顺带吃掉该段与下一段之间的那一行空行,不会留下多余空行。其余每一行(连同
    它原本的换行符)逐字保留,因此 CRLF / LF 不会被改动。
    """
    lines = text.splitlines(keepends=True)
    out = []
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].strip() == SECTION:
            i += 1  # 跳过该段标题
            while i < n and not lines[i].lstrip().startswith("["):
                i += 1  # 跳过段内每一行,直到下一个段标题
        else:
            out.append(lines[i])
            i += 1
    return "".join(out)


def strip_bytes(raw):
    """对原始字节做清理,保留可能存在的 UTF-8 BOM。遇到非 UTF-8 内容时,
    为避免破坏文件,原样返回。"""
    had_bom = raw.startswith(BOM)
    body = raw[len(BOM):] if had_bom else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    cleaned = strip_text(text).encode("utf-8")
    return (BOM + cleaned) if had_bom else cleaned


def iter_ass(paths):
    if paths:
        for p in paths:
            yield p
        return
    for root, dirs, files in os.walk("."):
        if ".git" in root.split(os.sep):
            dirs[:] = [d for d in dirs if d != ".git"]
            continue
        for name in files:
            if name.lower().endswith(".ass"):
                yield os.path.join(root, name)


def fix_in_place(path):
    with open(path, "rb") as f:
        raw = f.read()
    cleaned = strip_bytes(raw)
    if cleaned == raw:
        return False
    with open(path, "wb") as f:
        f.write(cleaned)
    return True


def has_section(path):
    with open(path, "rb") as f:
        raw = f.read()
    return strip_bytes(raw) != raw


def main(argv):
    args = argv[1:]

    if args and args[0] == "--filter":
        sys.stdout.buffer.write(strip_bytes(sys.stdin.buffer.read()))
        return 0

    if args and args[0] == "--check":
        bad = [p for p in iter_ass(args[1:]) if has_section(p)]
        for p in bad:
            print(p)
        if bad:
            sys.stderr.write("%d 个文件仍包含 %s\n" % (len(bad), SECTION))
            return 1
        return 0

    changed = [p for p in iter_ass(args) if fix_in_place(p)]
    for p in changed:
        print("cleaned: " + p)
    print("共清理 %d 个文件。" % len(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""改 config.yaml 里的单个键(保留注释和排版)，给一键安装脚本用。

  python tools/configure.py config.yaml server.host 127.0.0.1 server.port 8900 ...

只支持两级键(section.key)。值按 JSON 解析(true/8900/["a"]/"x")，解析不了按字符串写。
找到 section 下已有的 key 就原地替换值(行尾注释保留)，没有就插到 section 第一行后；
section 都没有就追加到文件末尾。改完重新解析一遍核对，对不上就不写盘。
"""
import json
import re
import sys

import yaml


def _value(raw):
    try:
        v = json.loads(raw)
    except ValueError:
        v = raw
    return v, json.dumps(v, ensure_ascii=False)


def set_key(text, section, key, literal):
    lines = text.splitlines(keepends=True)
    sec_re = re.compile(rf"^{re.escape(section)}:\s*(#.*)?$")
    start = next((i for i, ln in enumerate(lines) if sec_re.match(ln.rstrip("\r\n"))), None)
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += [f"\n{section}:\n", f"  {key}: {literal}\n"]
        return "".join(lines)
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and not ln.startswith((" ", "\t", "#")):
            end = i
            break
    key_re = re.compile(rf"^(\s+){re.escape(key)}:(\s*)([^#\r\n]*?)(\s*#.*)?(\r?\n)?$")
    for i in range(start + 1, end):
        m = key_re.match(lines[i])
        if m:
            comment = m.group(4) or ""
            lines[i] = f"{m.group(1)}{key}:{m.group(2) or ' '}{literal}{comment}{m.group(5) or chr(10)}"
            return "".join(lines)
    lines.insert(start + 1, f"  {key}: {literal}\n")
    return "".join(lines)


def main(argv):
    if len(argv) < 4 or len(argv) % 2:
        print(__doc__)
        return 2
    path = argv[1]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pairs = list(zip(argv[2::2], argv[3::2]))
    want = {}
    for dotted, raw in pairs:
        section, _, key = dotted.partition(".")
        if not section or not key:
            print(f"键必须是 section.key: {dotted}")
            return 2
        v, literal = _value(raw)
        text = set_key(text, section, key, literal)
        want[(section, key)] = v
    doc = yaml.safe_load(text) or {}
    for (section, key), v in want.items():
        if (doc.get(section) or {}).get(key) != v:
            print(f"改 {section}.{key} 后核对不一致，未写盘")
            return 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

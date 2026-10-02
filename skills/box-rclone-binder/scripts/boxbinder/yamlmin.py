"""Minimal YAML-subset parser (block mappings, block sequences, scalars, comments, quotes).

Enough for machines.yaml. Used only as a fallback when PyYAML is not installed, so the tool
stays stdlib-only and the test-suite is hermetic. Not a general YAML implementation:
no anchors, flow collections, multi-doc, or block scalars.
"""
from __future__ import annotations

import json


def _strip_comment(s: str) -> str:
    out, q = [], None
    for ch in s:
        if q:
            out.append(ch)
            if ch == q:
                q = None
        elif ch in ('"', "'"):
            q = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _scalar(tok: str):
    t = tok.strip()
    if t == "" or t == "~" or t.lower() == "null":
        return None
    if t.startswith('"'):
        return json.loads(t)
    if t.startswith("'"):
        if len(t) < 2 or not t.endswith("'"):
            raise ValueError('unterminated quoted scalar')
        return t[1:-1].replace("''", "'")
    if t[:1] in ('{', '[', '&', '*', '!', '|', '>'):
        raise ValueError('unsupported YAML scalar or collection')
    low = t.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def load(text: str):
    if text.lstrip().startswith(('{', '[')):
        def unique_mapping(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate mapping key')
                result[key] = value
            return result
        return json.loads(text, object_pairs_hook=unique_mapping)
    raw = []
    for ln in text.splitlines():
        c = _strip_comment(ln)
        if c.strip() == "":
            continue
        if c.strip() in ('---', '...') or '\t' in ln[:len(ln) - len(ln.lstrip())]:
            raise ValueError('unsupported YAML document marker or indentation')
        raw.append((_indent(c), c.strip(), c))
    pos = [0]

    def parse_block(min_indent):
        if pos[0] >= len(raw):
            return None
        indent = raw[pos[0]][0]
        if indent < min_indent:
            return None
        if raw[pos[0]][1].startswith("- "):
            return parse_seq(indent)
        return parse_map(indent)

    def parse_seq(indent):
        items = []
        while pos[0] < len(raw):
            cur_indent, body, _ = raw[pos[0]]
            if cur_indent != indent or not body.startswith("- "):
                break
            pos[0] += 1
            rest = body[2:].strip()
            if rest == "":
                items.append(parse_block(indent + 1))
            elif ":" in rest and not _is_quoted_scalar(rest):
                # inline first key of a mapping item; subsequent keys are deeper-indented
                k, v = _split_kv(rest)
                d = {}
                if v == "":
                    pos2 = pos[0]
                    child = parse_block(indent + 1)
                    d[k] = child
                else:
                    d[k] = _scalar(v)
                # continuation keys aligned deeper than the dash
                while pos[0] < len(raw) and raw[pos[0]][0] > indent and not raw[pos[0]][1].startswith("- "):
                    ci, b2, _ = raw[pos[0]]
                    kk, vv = _split_kv(b2)
                    if kk in d:
                        raise ValueError('duplicate mapping key')
                    pos[0] += 1
                    if vv == "":
                        d[kk] = parse_block(ci + 1)
                    else:
                        d[kk] = _scalar(vv)
                items.append(d)
            else:
                items.append(_scalar(rest))
        return items

    def parse_map(indent):
        d = {}
        while pos[0] < len(raw):
            cur_indent, body, _ = raw[pos[0]]
            if cur_indent != indent or body.startswith("- "):
                break
            k, v = _split_kv(body)
            if k in d:
                raise ValueError('duplicate mapping key')
            pos[0] += 1
            if v == "":
                child = parse_block(indent + 1)
                d[k] = child if child is not None else None
            else:
                d[k] = _scalar(v)
        return d

    def _split_kv(s):
        # split on first ':' outside quotes
        q = None
        for i, ch in enumerate(s):
            if q:
                if ch == q:
                    q = None
            elif ch in ('"', "'"):
                q = ch
            elif ch == ":":
                key = s[:i].strip()
                if key.startswith(('"', "'")):
                    key = _scalar(key)
                if not isinstance(key, str) or not key or key == '<<':
                    raise ValueError('unsupported mapping key')
                return key, s[i + 1:].strip()
        raise ValueError('mapping entry requires a colon')

    def _is_quoted_scalar(s):
        s = s.strip()
        return len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'")

    result = parse_block(0)
    if pos[0] != len(raw):
        raise ValueError('unsupported indentation or trailing YAML content')
    return result

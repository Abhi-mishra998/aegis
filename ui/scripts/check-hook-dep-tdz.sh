#!/usr/bin/env bash
# Asserts no useEffect/useMemo/useCallback in a default-exported
# component references an identifier in its dep array whose `const`
# declaration appears LATER in the same function body.
#
# Catches the bug class that bricked /incidents on 2026-06-24 (incident
# 664fb8d5): a no-blink refactor moved the SSE-debounce const
# declaration BELOW a useEffect that put `debouncedRefresh` in its dep
# array. The dep array is evaluated immediately when the component body
# runs top-to-bottom, but `const` is in the Temporal Dead Zone until its
# declaration line, so the page threw
# `ReferenceError: Cannot access 'O' before initialization` in
# production. Vite's minifier shortened the identifier to `O` which
# made the error look mysterious.
#
# The check inspects the default-exported component's function body,
# finds top-level `const X = …` declarations, and for every
# useEffect/useMemo/useCallback at depth 0 checks whether the
# identifiers in its dep array appear later in the function body.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - "$PWD/src" <<'PY'
import re, os, sys

ROOT = sys.argv[1]
HOOKS = ('useEffect', 'useMemo', 'useCallback')

def find_function_body(src, fn_start):
    """Given offset of `{` opening function body, return (body_start, body_end)."""
    depth = 1
    i = fn_start + 1
    while i < len(src) and depth > 0:
        if src[i] == '{': depth += 1
        elif src[i] == '}': depth -= 1
        i += 1
    return fn_start + 1, i - 1

def find_hook_end(src, hook_open_paren):
    """Return offset of the closing `)` matching `(` at hook_open_paren."""
    depth = 1
    i = hook_open_paren + 1
    in_str = None
    while i < len(src) and depth > 0:
        c = src[i]
        if in_str:
            if c == '\\' and i + 1 < len(src):
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ('"', "'", '`'):
            in_str = c
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

def top_level_consts(body):
    """Return {name: offset_within_body} for `const|let|var X = …` declared at depth 0."""
    decls = {}
    bd = 0
    in_str = None
    i = 0
    while i < len(body):
        c = body[i]
        if in_str:
            if c == '\\' and i + 1 < len(body):
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ('"', "'", '`'):
            in_str = c
        elif c == '{':
            bd += 1
        elif c == '}':
            bd -= 1
        elif bd == 0:
            mm = re.match(r'(?:const|let|var)\s+(\w+)\s*=', body[i:])
            if mm and (i == 0 or body[i - 1] in ' \t\n;'):
                decls[mm.group(1)] = i
        i += 1
    return decls

violations = []
for dirpath, _, files in os.walk(ROOT):
    for fn in files:
        if not fn.endswith(('.jsx', '.tsx', '.js', '.ts')):
            continue
        f = os.path.join(dirpath, fn)
        src = open(f).read()
        for fmatch in re.finditer(r'export default function\s+\w+\s*\([^)]*\)\s*\{', src):
            body_start, body_end = find_function_body(src, fmatch.end() - 1)
            body = src[body_start:body_end]
            decls = top_level_consts(body)
            for hmatch in re.finditer(r'\b(use(?:Effect|Memo|Callback))\s*\(', body):
                pos = hmatch.start()
                # depth at hook call site
                bd2 = 0
                in_s = None
                for k in range(pos):
                    c = body[k]
                    if in_s:
                        if c == '\\':
                            continue
                        if c == in_s: in_s = None
                        continue
                    if c in ('"', "'", '`'): in_s = c
                    elif c == '{': bd2 += 1
                    elif c == '}': bd2 -= 1
                if bd2 != 0:
                    continue
                open_paren = hmatch.end() - 1
                close_paren = find_hook_end(body, open_paren)
                if close_paren < 0:
                    continue
                args = body[open_paren + 1:close_paren]
                # Find LAST `, [ ... ]` in args (the dep array).
                dep_match = re.search(r',\s*\[([^\]]*)\]\s*$', args, re.DOTALL)
                if not dep_match:
                    continue
                for ident in re.findall(r'\b\w+\b', dep_match.group(1)):
                    if ident in decls and decls[ident] > pos:
                        line = src[:body_start + pos].count('\n') + 1
                        decl_line = src[:body_start + decls[ident]].count('\n') + 1
                        rel = os.path.relpath(f, os.path.dirname(ROOT))
                        violations.append((rel, line, hmatch.group(1), ident, decl_line))

if violations:
    print(f"FAILED: {len(violations)} hook-dep TDZ violation(s) found", file=sys.stderr)
    for v in violations:
        print(f"  ERROR: {v[0]}:{v[1]}  {v[2]} dep '{v[3]}' declared LATER at line {v[4]}", file=sys.stderr)
    sys.exit(1)
print("✓ no hook-dep TDZ ordering issues")
PY

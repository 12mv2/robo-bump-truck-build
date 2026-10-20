#!/usr/bin/env python3
"""check.py — the build repo's close/check gate. Run before every commit and at
every session close. SENSE, DO NOT ACT: it never edits, deletes or commits.
Exit 1 on any FAIL. A check is added only after a real incident, with a dated why.

why (2026-10-10): this repo will be public. Every line is written as if a hiring
manager, a peer and the partner all read it. How the work is done — handoffs,
process diaries, orchestration, people, partners, passwords, network, drive
layout — lives at ~/Projects/clients/robo-bump-truck-build/ and never here.
Publish rules: see PUBLISH-POLICY.md in the private client folder (not in this repo).
Images: every published photo goes through scripts/strip-image-metadata.sh; originals are never the published file.
Do not use `git commit -v` in this repo (the commit-msg scan reads the whole message file)."""
import re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_lib  # the one shared implementation (denylist, normaliser, scans); also used by the pre-push hook

REPO = Path(__file__).resolve().parent.parent
PRIVATE_STRINGS_FILE = gate_lib.DENY_FILE
BIG_FILE_MB = gate_lib.BIG_FILE_MB
OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
failed = []
FLIP = "--flip" in sys.argv  # flip-to-public run: placeholders and unfilled photo slots FAIL instead of WARN
PLACEHOLDER_PHRASES = re.compile(r"\bnot yet stated\b|\bto follow\b(?=\s*(?:\||$|[.;)]))", re.I)  # "to follow" only as an ending
PHOTO_SLOT = re.compile(r"\bphoto\s*:", re.I)  # an unfilled image slot, anywhere on the line

def report(status, check, *lines):
    print("[%s] %s" % (status, check))
    for l in lines:
        print("       " + l)
    if status == FAIL:
        failed.append(check)

def sh(args):
    return subprocess.run(args, cwd=str(REPO), capture_output=True, text=True, env=gate_lib.git_env()).stdout

def gitx(args):
    # raising variant of sh(): a git failure must surface as a FAIL, never as empty output
    return subprocess.run(args, cwd=str(REPO), capture_output=True, text=True, check=True,
                          env=gate_lib.git_env()).stdout

def tracked_and_pending():
    # -z throughout: quoted/escaped names must not hide a path from the scans
    files = set(gitx(["git", "ls-files", "-z"]).split("\0"))
    ents = gitx(["git", "status", "--porcelain", "-uall", "-z"]).split("\0")
    i = 0
    while i < len(ents):
        e = ents[i]
        i += 1
        if len(e) > 3 and "D" not in e[:2]:
            files.add(e[3:])
        if e[:1] in ("R", "C") or e[1:2] in ("R", "C"):
            i += 1  # the rename/copy origin path follows as its own entry
    return sorted(f for f in files if f)

def push_gate():
    # why (2026-10-10): a stub, a stale copy or a non-executable hook passes silently - compare bytes.
    # why (2026-10-10, review B14v): a RELATIVE hooksPath resolves in whatever worktree is checked out,
    # so an old checkout has no hook at all - the path must be absolute and be the main checkout's scripts/hooks (shared by its worktrees).
    probs = gate_lib.hook_problems()
    cfg = sh(["git", "config", "--get", "core.hooksPath"]).strip()
    want = gate_lib.HOOKS_DIR.resolve()   # the main checkout's hooks, from any worktree
    if not Path(cfg).is_absolute():
        probs.append("core.hooksPath is %r: it must be an absolute path (a relative one resolves in each worktree)" % cfg)
    elif Path(cfg).resolve() != want:
        probs.append("core.hooksPath is %r, which is not the main checkout's scripts/hooks (%s)" % (cfg, want))
    if probs:
        report(FAIL, "push_gate", *(probs + ["fix: git config core.hooksPath %s (once per clone); restore the hook or update gate_lib.HOOK_SHA256 in the same commit" % want]))
    else:
        report(OK, "push_gate", "core.hooksPath=%s (absolute); pre-commit, commit-msg and pre-push present, executable and matching their SHA-256" % cfg)

def denylist():
    stats = {}
    try:
        deny = gate_lib.load_denylist(stats=stats)
    except gate_lib.GateError as e:
        report(FAIL, "denylist", str(e))
        return []
    line = ("%d entries loaded (%d regex; %d comment lines '# ', %d blank, %d dropped as empty after normalising; "
            "file stays outside the repo)"
            % (len(deny), sum(1 for e in deny if e.kind == "re"), stats["comments"], stats["blank"], stats["dropped"]))
    report(WARN if stats["dropped"] else OK, "denylist", line,
           *(["a dropped entry matches nothing: fix or remove it"] if stats["dropped"] else []))
    return deny

def private_material(deny):
    files = tracked_and_pending()
    probs = gate_lib.audit_files(REPO, files, deny, limit_mb=10 ** 9)  # size is big_files' job
    probs += gate_lib.audit_staged(REPO, deny, limit_mb=10 ** 9)  # the index, not just the working copy
    if probs:
        report(FAIL, "private_material", *(probs[:30] + ["fix: move it to ~/Projects/clients/robo-bump-truck-build/ — this repo is deliverables only"]))
    else:
        report(OK, "private_material", "no private paths, strings, opaque content or unlisted binaries in %d tracked/pending files" % len(files))

def history(deny):
    # why (2026-10-10): the working tree is not what gets pushed - commits are. Same rules as pre-push,
    # over every commit and ref: identities, messages, paths, blobs, tags, sizes.
    repo = str(REPO)
    commits = gitx(["git", "rev-list", "--all"]).split()
    probs = gate_lib.audit_commits(repo, commits, deny)
    for ent in gitx(["git", "for-each-ref", "--format=%(refname) %(objectname)"]).splitlines():
        ref, sha = ent.split()
        probs += gate_lib.audit_ref(repo, ref, sha, deny)
    if probs:
        report(FAIL, "history", *(probs[:30] + ["fix: rewrite the offending commit(s) before the first push"]))
    else:
        report(OK, "history", "%d commit(s) clean: raw commit objects, paths, blobs, refs, tags, sizes" % len(commits))

def placeholders():
    # why (2026-10-10): the bar is "no placeholders" — a TODO or TBD in a public page is a broken promise
    hits = []
    for f in tracked_and_pending():
        fp = REPO / f
        if not fp.is_file() or not f.endswith((".md", ".html")) or f.startswith("scripts/"):
            continue
        for i, line in enumerate(fp.read_text(errors="replace").splitlines(), 1):
            u = line.upper()
            if ("TODO" in u or "TBD" in u or "XXX" in u or "FIXME" in u or "LOREM" in u
                    or PLACEHOLDER_PHRASES.search(line)
                    or (FLIP and PHOTO_SLOT.search(line))):
                hits.append("%s:%d: %s" % (f, i, line.strip()[:80]))
    if hits:
        report(FAIL if FLIP else WARN, "placeholders", *(hits[:20] + ["fix: write the sentence or delete the line"]))
    else:
        report(OK, "placeholders", "no TODO/TBD/FIXME/'not yet stated'/'to follow'%s in pages" % (" or photo: slots" if FLIP else ""))

def big_files():
    big = gate_lib.oversize(REPO, tracked_and_pending(), BIG_FILE_MB)
    if big:
        report(FAIL, "big_files", *(big + ["fix: leave media on the Crucial drive and link a release asset"]))
    else:
        report(OK, "big_files", "nothing tracked, staged or untracked over %d MB" % BIG_FILE_MB)

def indexes():
    bad = []
    for idx in REPO.rglob("INDEX.md"):
        if ".git" in idx.parts:
            continue
        text = idx.read_text(errors="replace")
        for f in idx.parent.iterdir():
            if f.name in ("INDEX.md", ".DS_Store") or f.name.startswith("."):
                continue
            if f.name not in text and f.stem not in text:
                bad.append("%s lacks %s" % (idx.relative_to(REPO), f.name))
    if bad:
        report(FAIL, "indexes", *(bad + ["fix: add one line per file"]))
    else:
        report(OK, "indexes", "every INDEX.md names every sibling")

def working_tree():
    st = sh(["git", "status", "--porcelain"]).strip()
    head = sh(["git", "log", "-1", "--format=%h %s"]).strip() or "(no commits)"
    br = sh(["git", "branch", "--show-current"]).strip()
    vis = sh(["gh", "repo", "view", "--json", "visibility", "-q", ".visibility"]).strip() or "(no remote)"
    report(WARN if st else OK, "working_tree", "branch %s @ %s · GitHub visibility: %s" % (br, head, vis),
           *(["uncommitted:"] + st.splitlines()[:15] if st else ["clean"]))

def text_mode(path):
    # --text FILE: the same plain-text rules (opaque content, denylist) on one arbitrary file;
    # exit 1 on a problem, 2 if unusable
    try:
        deny = gate_lib.load_denylist()
        data = Path(path).read_bytes()
    except (gate_lib.GateError, OSError) as e:
        print("[FAIL] text: %s" % e)
        return 2
    probs = gate_lib.bytes_problems(data, deny, path)
    if probs:
        for p in probs:
            print("[FAIL] text %s" % p)
        return 1
    print("[OK  ] text %s: no denylist hit, nothing opaque" % path)
    return 0

_args = sys.argv[1:]
_bad = [a for i, a in enumerate(_args) if a not in ("--flip", "--text") and not (i and _args[i - 1] == "--text")]
if _bad:
    print("usage: check.py [--flip] | check.py --text FILE   (unknown argument: %s)" % " ".join(_bad))
    sys.exit(2)
if "--text" in sys.argv and FLIP:
    print("usage: --flip cannot be combined with --text (--text FILE applies no page rules); run them separately")
    sys.exit(2)

if "--text" in sys.argv:
    i = sys.argv.index("--text")
    if i + 1 >= len(sys.argv):
        print("usage: check.py --text FILE")
        sys.exit(2)
    sys.exit(text_mode(sys.argv[i + 1]))

words = []
for c in (push_gate, denylist, private_material, history, placeholders, big_files, indexes, working_tree):
    try:
        if c is denylist:
            words = c()
        elif c in (private_material, history):
            if not words:  # no denylist, no scan: never print OK for a scan that did not run
                report(FAIL, c.__name__, "not run: the denylist is missing or empty")
                continue
            c(words)
        else:
            c()
    except Exception as e:
        report(FAIL, c.__name__, "check errored: %r" % e)
sys.exit(1 if failed else 0)

#!/usr/bin/env python3
"""Create a GitHub repository and upload this skill directory through the GitHub API.

Why the API instead of git: only ``api.github.com`` may be reachable on some
corporate/host networks while ``github.com:443`` (used by git push) is blocked.
The API path needs a Personal Access Token — GitHub does not accept account
passwords for API or git operations.

Usage
    set GITHUB_TOKEN=ghp_xxx            (Windows cmd)
    $env:GITHUB_TOKEN = "ghp_xxx"       (PowerShell)
    python tools/publish_github.py --repo cdd-writing-skill

Token scopes: classic token needs ``repo`` (create repository + contents).
Fine-grained token needs "Administration: write" (create repo) and
"Contents: write" (upload files) on the target account.

Options
    --repo NAME      repository name (default: cdd-writing-skill)
    --private        create a private repository
    --dir PATH       directory to upload (default: the skill root, i.e. parent of tools/)
    --branch NAME    branch to commit to (default: main)
    --message TEXT   commit message
    --dry-run        print what would be created/uploaded, do not call the API
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
SKIP_DIRS = {".git", "__pycache__", ".idea", ".vscode", "node_modules"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".bak", ".tmp")


def request(method, path, token, payload=None):
    url = API + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cdd-writing-skill-publisher",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return error.code, parsed


def collect_files(root):
    files = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
        for name in sorted(names):
            if name.endswith(SKIP_SUFFIXES) or name in (".DS_Store", "Thumbs.db"):
                continue
            full = os.path.join(current, name)
            relative = os.path.relpath(full, root).replace(os.sep, "/")
            files.append((relative, full))
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="cdd-writing-skill")
    parser.add_argument("--private", action="store_true")
    parser.add_argument("--dir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--branch", default="main")
    parser.add_argument("--message", default="Add cdd-writing skill")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.dir)
    if not os.path.isdir(root):
        raise SystemExit("directory not found: %s" % root)
    files = collect_files(root)
    print("skill directory: %s" % root)
    print("files to upload: %d" % len(files))
    for relative, _ in files:
        print("  " + relative)
    if args.dry_run:
        print("\nDRY RUN — set GITHUB_TOKEN and drop --dry-run to publish")
        return 0

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("GITHUB_TOKEN is not set (create a Personal Access Token with repo scope)")

    status, user = request("GET", "/user", token)
    if status != 200:
        raise SystemExit("token rejected (HTTP %s): %s" % (status, user))
    login = user["login"]
    print("authenticated as: %s" % login)

    status, repo = request("POST", "/user/repos", token, {
        "name": args.repo,
        "private": bool(args.private),
        "description": "DSH skill: inspect and correct CANdela CDD diagnostic descriptions",
        "auto_init": False,
    })
    if status == 201:
        print("created repository: %s" % repo["html_url"])
    elif status == 422:
        print("repository already exists, uploading into it: %s/%s" % (login, args.repo))
    else:
        raise SystemExit("could not create repository (HTTP %s): %s" % (status, repo))

    uploaded = 0
    for relative, full in files:
        with open(full, "rb") as handle:
            content = base64.b64encode(handle.read()).decode("ascii")
        path = "/repos/%s/%s/contents/%s" % (login, args.repo, relative)
        sha = None
        status, existing = request("GET", path + "?ref=" + args.branch, token)
        if status == 200 and isinstance(existing, dict):
            sha = existing.get("sha")
        payload = {"message": args.message, "content": content, "branch": args.branch}
        if sha:
            payload["sha"] = sha
        status, result = request("PUT", path, token, payload)
        if status in (200, 201):
            uploaded += 1
            print("  uploaded %s" % relative)
        else:
            print("  FAILED   %s (HTTP %s): %s" % (relative, status, result))
    print("\nrepository: https://github.com/%s/%s" % (login, args.repo))
    print("uploaded %d/%d files" % (uploaded, len(files)))
    return 0 if uploaded == len(files) else 1


if __name__ == "__main__":
    sys.exit(main())

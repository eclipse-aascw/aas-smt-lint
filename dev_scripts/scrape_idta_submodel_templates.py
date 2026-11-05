# SPDX-FileCopyrightText: 2025 aas-smt-lint AUTHORS
#
# SPDX-License-Identifier: MIT

"""Download all the IDTA submodel templates from GitHub which were previously listed."""

import os.path
import sys
import time
import pathlib
import urllib.parse
import uuid

import requests
from icontract import ensure


def github_blob_to_raw(url: str) -> str:
    """
    Convert a GitHub 'blob' URL to the corresponding raw URL.

    Example:
      https://github.com/<owner>/<repo>/blob/<sha>/<path>
    -> https://raw.githubusercontent.com/<owner>/<repo>/<sha>/<path>
    """
    parts = urllib.parse.urlsplit(url)
    if parts.netloc != "github.com" or "/blob/" not in parts.path:
        return url

    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 5 or segments[2] != "blob":
        raise ValueError(f"Unexpected GitHub blob URL format: {url}")

    owner, repo, _, sha = segments[:4]
    path_after_sha = "/".join(segments[4:])
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path_after_sha}"


@ensure(lambda result: result.endswith(".json"))
def target_filename_from_url(url: str) -> str:
    """
    Derive a readable local filename from the URL's last path segment.
    """
    last = urllib.parse.unquote(
        pathlib.Path(urllib.parse.urlsplit(url).path).name
    ).strip()
    if not last:
        last = "download.json"
    if not last.lower().endswith(".json"):
        last += ".json"
    return last


def download_with_retries(url: str, dest: pathlib.Path) -> None:
    """Download the file at ``url`` with retries and store it to ``dest``."""
    headers = {
        "Accept": "application/vnd.github.raw+json,application/json;q=0.9,*/*;q=0.8",
        "User-Agent": "python-requests",
    }
    max_retries = 3
    timeout = 30

    for attempt in range(1, max_retries + 1):
        # noinspection PyBroadException
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return
        except Exception:
            if attempt == max_retries:
                raise
            time.sleep(0.5 * attempt)


def main() -> int:
    """Execute the main routine."""
    repo_root = pathlib.Path(os.path.realpath(__file__)).parent.parent
    list_path = repo_root / "test_data/idta_submodel_templates/url_list.txt"
    out_dir = repo_root / "test_data/idta_submodel_templates/originals"

    if not list_path.exists():
        print(f"List file not found: {list_path}", file=sys.stderr)
        sys.exit(1)

    lines = [
        line
        for line in list_path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    ]
    urls = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]

    successes = 0
    failures = []

    sleep_between = 0.5

    print(f"Found {len(urls)} URL(s) in {list_path}")
    for i, original_url in enumerate(urls, start=1):
        raw_url = github_blob_to_raw(original_url)
        filename = target_filename_from_url(original_url)
        dest = out_dir / filename
        dest_tmp = out_dir / f"{filename}.{uuid.uuid4()}.tmp"

        try:
            print(f"[{i}/{len(urls)}] Downloading -> {dest}")
            download_with_retries(raw_url, dest_tmp)
            dest_tmp.rename(dest)
            successes += 1
            time.sleep(sleep_between)
        except Exception as e:
            dest_tmp.unlink(missing_ok=True)
            failures.append((original_url, str(e)))
            print(f"  !! Failed: {original_url}\n     Reason: {e}", file=sys.stderr)

    print(f"Done. Downloaded {successes}/{len(urls)} file(s) to {out_dir}")
    if failures:
        print("Failures:")
        for u, err in failures:
            print(f" - {u}\n   {err}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

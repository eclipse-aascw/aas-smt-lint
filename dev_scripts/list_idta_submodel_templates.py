"""List the URLs of all IDTA submodel templates on GitHub."""

import pathlib
import sys
from typing import List
import urllib.parse

import requests


def main() -> int:
    """Execute the main routine."""
    owner = "admin-shell-io"
    repo = "submodel-templates"
    branch = "main"

    headers = {"Accept": "application/vnd.github+json"}

    branch_url = f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}"
    branch_resp = requests.get(branch_url, headers=headers, timeout=30)
    branch_resp.raise_for_status()
    commit_sha = branch_resp.json()["commit"]["sha"]

    trees_url = (
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    )

    response = requests.get(trees_url, headers=headers, timeout=30)
    response.raise_for_status()

    tree = response.json()["tree"]

    json_files = [
        item["path"] for item in tree if item["path"].endswith(".json")
    ]  # type: List[str]

    permalinks = []  # type: List[str]
    for path in json_files:
        encoded_path = "/".join(
            urllib.parse.quote(segment) for segment in pathlib.Path(path).parts
        )

        permalink = (
            f"https://github.com/{owner}/{repo}/blob/{commit_sha}/{encoded_path}"
        )
        permalinks.append(permalink)

    permalinks.sort()

    print("\n".join(permalinks))

    return 0


if __name__ == "__main__":
    sys.exit(main())

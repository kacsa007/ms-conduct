"""A mutable release points at one authenticated, encrypted SQLite snapshot.

Upload first, then switch the release body in one request. A failed upload
leaves the last good pointer usable. Only this workflow writes the release.
"""

import gzip
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

import requests


TAG = "reddit-data-v1"
ASSET = re.compile(r"discussion-[0-9]+-[0-9]+\.sqlite\.gz\.fernet\Z")
INITIAL = "Initializing the Reddit discussion database."
DEFAULT_REPO = "kacsa007/ms-conduct"


class Archive:
    def __init__(self, repo=DEFAULT_REPO, token=None):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise ValueError("Invalid GitHub repository")
        self.base = "https://api.github.com/repos/" + repo
        self.upload_base = "https://uploads.github.com/repos/" + repo
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/vnd.github+json"
        if token:
            self.session.headers["Authorization"] = "Bearer " + token

    def release(self):
        response = self.session.get(self.base + "/releases/tags/" + TAG, timeout=20)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    @staticmethod
    def manifest(release):
        if not release or release["body"] == INITIAL:
            return None
        result = json.loads(release["body"])
        if result["version"] != 1 or not ASSET.fullmatch(result["asset"]):
            raise ValueError("Unrecognized Reddit archive manifest")
        return result

    def restore(self, release, path, cipher):
        manifest = self.manifest(release)
        if manifest is None:
            return False
        asset = next(a for a in release["assets"] if a["name"] == manifest["asset"])
        r = self.session.get(
            self.base + "/releases/assets/" + str(asset["id"]),
            headers={"Accept": "application/octet-stream"},
            timeout=(5, 75),
        )
        r.raise_for_status()
        from cryptography.fernet import InvalidToken

        try:
            raw = gzip.decompress(cipher.decrypt(r.content))
        except InvalidToken:
            raise ValueError(
                "REDDIT_DB_KEY cannot decrypt the published snapshot"
            ) from None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            temp = Path(tmp.name)
        try:
            temp.write_bytes(raw)
            with sqlite3.connect(temp) as db:
                if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise ValueError("Invalid SQLite snapshot")
                db.execute("SELECT name FROM items LIMIT 1")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        return True

    def publish(self, release, path, cipher, report, run, attempt):
        name = f"discussion-{run}-{attempt}.sqlite.gz.fernet"
        if not ASSET.fullmatch(name):
            raise ValueError("Invalid Actions run identifier")
        previous = self.manifest(release)
        payload = cipher.encrypt(
            gzip.compress(Path(path).read_bytes(), compresslevel=4)
        )
        if release is None:
            r = self.session.post(
                self.base + "/releases",
                timeout=25,
                json={
                    "tag_name": TAG,
                    "name": "Reddit discussion database",
                    "body": INITIAL,
                    "prerelease": True,
                    "make_latest": "false",
                },
            )
            r.raise_for_status()
            release = r.json()
        release_path = "/releases/" + str(release["id"])
        r = self.session.post(
            self.upload_base + release_path + "/assets",
            params={"name": name},
            headers={"Content-Type": "application/octet-stream"},
            data=payload,
            timeout=(5, 90),
        )
        r.raise_for_status()
        asset = r.json()
        if asset.get("state") != "uploaded" or asset.get("size") != len(payload):
            raise ValueError("GitHub did not confirm the complete encrypted upload")
        latest = self.release()
        if latest is None or self.manifest(latest) != previous:
            raise RuntimeError(
                "Release changed during collection. Previous snapshot retained."
            )
        body = json.dumps(
            {"version": 1, "asset": name, "report": report}, separators=(",", ":")
        )
        r = self.session.patch(
            self.base + release_path, json={"body": body}, timeout=25
        )
        r.raise_for_status()
        # Also clear orphaned uploads from interrupted runs once a replacement is durable.
        for old in latest["assets"]:
            if old["name"] != name and ASSET.fullmatch(old["name"]):
                r = self.session.delete(
                    self.base + "/releases/assets/" + str(old["id"]), timeout=20
                )
                r.raise_for_status()


def cipher_from_env():
    from cryptography.fernet import Fernet

    return Fernet(os.environ["REDDIT_DB_KEY"].strip().encode("ascii"))

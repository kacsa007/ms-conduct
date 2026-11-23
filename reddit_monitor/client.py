"""App-only Reddit OAuth client with a single budget shared by all requests."""

import os
import time

import requests


class BudgetReached(Exception):
    pass


class RedditError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Reddit returned HTTP {status}")


class Reddit:
    def __init__(self, limit=100, seconds=450):
        self.limit = limit
        self.deadline = time.monotonic() + seconds
        self.used = 0
        self.next_call = 0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = os.environ["REDDIT_USER_AGENT"]
        result = self.request(
            "POST",
            "https://www.reddit.com/api/v1/access_token",
            auth=(os.environ["REDDIT_CLIENT_ID"], os.environ["REDDIT_CLIENT_SECRET"]),
            data={"grant_type": "client_credentials"},
        )
        if not result.get("access_token"):
            raise RuntimeError("Reddit did not issue an app access token")
        self.session.headers["Authorization"] = "Bearer " + result["access_token"]

    def request(self, method, url, **kwargs):
        for attempt in range(3):
            wait = max(0, self.next_call - time.monotonic())
            if self.used >= self.limit or time.monotonic() + wait + 15 >= self.deadline:
                raise BudgetReached("request or time budget reached")
            time.sleep(wait)
            self.used += 1
            self.next_call = time.monotonic() + 1
            try:
                response = self.session.request(
                    method, url, timeout=(4, 10), allow_redirects=False, **kwargs
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 2:
                    raise RuntimeError(
                        "Reddit connection failed after retries"
                    ) from None
                self.next_call = time.monotonic() + 2 ** (attempt + 1)
                continue
            remaining = response.headers.get("X-Ratelimit-Remaining")
            reset = float(response.headers.get("X-Ratelimit-Reset") or 0)
            if remaining is not None and float(remaining) < 2:
                self.next_call = max(self.next_call, time.monotonic() + reset + 1)
            status = response.status_code
            if status == 200:
                return response.json()
            if status not in {429, 500, 502, 503, 504} or attempt == 2:
                raise RedditError(status)
            delay = max(
                float(response.headers.get("Retry-After") or 0),
                reset if status == 429 else 0,
                2 ** (attempt + 1),
            )
            self.next_call = max(self.next_call, time.monotonic() + delay)
        raise RuntimeError("Reddit request failed")

    def get(self, path, **params):
        return self.request(
            "GET", "https://oauth.reddit.com" + path, params={"raw_json": 1, **params}
        )

"""Reads YouTube and Instagram and updates data.json. GitHub Actions runs this when the page's button asks.

Only reads. Both logins come from GitHub secrets, and errors print only their type so a token
can never reach the public log. Campaign, status and money come from the owner's daily routine.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(__file__).parent / "data.json"
GRAPH = "https://graph.facebook.com/v26.0"
YOUTUBE = "https://www.googleapis.com/youtube/v3"


def get(url: str, token: str, **params) -> dict:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
    request = urllib.request.Request(f"{url}?{query}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def youtube_token() -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
            "refresh_token": os.environ["YOUTUBE_READONLY_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        }
    ).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=60) as response:
        return json.load(response)["access_token"]


def youtube() -> tuple[dict, list[dict]]:
    token = youtube_token()
    channel = get(f"{YOUTUBE}/channels", token, part="statistics,contentDetails", mine="true")["items"][0]
    ids, page = [], None
    while True:
        response = get(
            f"{YOUTUBE}/playlistItems",
            token,
            part="contentDetails",
            playlistId=channel["contentDetails"]["relatedPlaylists"]["uploads"],
            maxResults=50,
            pageToken=page,
        )
        ids += [item["contentDetails"]["videoId"] for item in response["items"]]
        page = response.get("nextPageToken")
        if not page:
            break
    posts = []
    # The videos call takes at most 50 ids.
    for start in range(0, len(ids), 50):
        response = get(f"{YOUTUBE}/videos", token, part="statistics,snippet", id=",".join(ids[start : start + 50]))
        for video in response["items"]:
            stats = video["statistics"]
            posts.append(
                {
                    "url": f"https://www.youtube.com/shorts/{video['id']}",
                    "channel": "YouTube",
                    "title": video["snippet"]["title"],
                    "posted": video["snippet"]["publishedAt"][:10],
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                }
            )
    statistics = channel["statistics"]
    return {
        "channel": "YouTube",
        "posts": int(statistics.get("videoCount", 0)),
        "followers": int(statistics.get("subscriberCount", 0)),
        "views": sum(post["views"] for post in posts),
        "likes": sum(post["likes"] for post in posts),
    }, posts


def instagram(known: dict[str, dict]) -> tuple[dict, list[dict]]:
    token = os.environ["INSTAGRAM_TOKEN"]
    pages = get(f"{GRAPH}/me/accounts", token, fields="instagram_business_account{id}")["data"]
    account = next(page["instagram_business_account"]["id"] for page in pages if "instagram_business_account" in page)
    info = get(f"{GRAPH}/{account}", token, fields="followers_count,media_count")
    posts, after = [], None
    while True:
        response = get(f"{GRAPH}/{account}/media", token, fields="caption,permalink,timestamp,like_count", limit=50, after=after)
        for item in response.get("data", []):
            post = {
                "url": item["permalink"],
                "channel": "Instagram",
                "title": (item.get("caption") or item["permalink"]).split("\n")[0][:100],
                "posted": item["timestamp"][:10],
                "likes": int(item.get("like_count", 0)),
            }
            try:
                insights = get(f"{GRAPH}/{item['id']}/insights", token, metric="views")
                post["views"] = int(insights["data"][0]["values"][0]["value"])
            except Exception:
                # A missing number isn't a zero, so the last known views stay.
                post["views"] = (known.get(item["permalink"]) or {}).get("views") or 0
            posts.append(post)
        paging = response.get("paging", {})
        after = paging.get("cursors", {}).get("after") if paging.get("next") else None
        if not after:
            break
    return {
        "channel": "Instagram",
        "posts": int(info.get("media_count", 0)),
        "followers": int(info.get("followers_count", 0)),
        "views": sum(post["views"] for post in posts),
        "likes": sum(post["likes"] for post in posts),
    }, posts


def main() -> None:
    data = json.loads(DATA.read_text())
    before = json.dumps({key: data[key] for key in ("channels", "posts")}, sort_keys=True)
    known = {post["url"]: post for post in data["posts"]}
    # One channel failing, such as an expired login, must not stop the other.
    for name, read in (("YouTube", youtube), ("Instagram", lambda: instagram(known))):
        try:
            summary, posts = read()
        except Exception as error:
            print(f"{name} wasn't updated: {type(error).__name__}")
            continue
        today = datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d")
        data["channels"] = [row for row in data["channels"] if row["channel"] != name] + [{"date": today, **summary}]
        for post in posts:
            known.setdefault(post["url"], {}).update(post)
        print(f"{name}: {summary['views']} views, {summary['posts']} posts")
    data["channels"].sort(key=lambda row: row["channel"])
    data["posts"] = sorted(known.values(), key=lambda post: post.get("posted") or "", reverse=True)
    # The date on channel rows changes daily, so only real numbers count as a change.
    strip = lambda rows: [{key: value for key, value in row.items() if key != "date"} for row in rows]
    if json.dumps({"channels": strip(data["channels"]), "posts": data["posts"]}, sort_keys=True) != json.dumps(
        {"channels": strip(json.loads(before)["channels"]), "posts": json.loads(before)["posts"]}, sort_keys=True
    ):
        data["updated"] = datetime.now(ZoneInfo("America/Mexico_City")).strftime("%d/%m/%Y %H:%M")
        DATA.write_text(json.dumps(data, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

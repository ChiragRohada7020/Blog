"""Live end-to-end check of the Telegram image store.

Unlike test_media.py, this talks to the real Telegram API, so it catches things
mocks cannot: a wrong token, a wrong chat id, or Telegram's own response
headers. It deliberately does not touch MongoDB or send any email.

NOTE: this uploads one image to your Telegram chat each time it runs.

Run:  python tests/live_telegram_check.py
"""

import io
import os
import sys

from werkzeug.datastructures import FileStorage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as blog  # noqa: E402

SOURCE = os.path.join("public", "static", "images", "post_images", "main.jpg")


def main():
    print("telegram_enabled :", blog.telegram_enabled())
    print("chat id          :", blog.TELEGRAM_CHAT_ID)
    if not blog.telegram_enabled():
        print("\nSet TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first.")
        return 1

    with open(SOURCE, "rb") as handle:
        original = handle.read()
    print("source image     : %s (%d bytes)" % (SOURCE, len(original)))

    storage = FileStorage(stream=io.BytesIO(original), filename="livecheck.jpg",
                          content_type="image/jpeg")
    reference = blog.telegram_upload(storage)
    print("stored reference :", reference)

    response = blog.app.test_client().get("/media/" + reference[len(blog.TG_PREFIX):])
    body = response.data

    print("media status     :", response.status_code)
    print("content-type     :", response.headers.get("Content-Type"))
    print("disposition      :", response.headers.get("Content-Disposition"))
    print("cache-control    :", response.headers.get("Cache-Control"))
    print("bytes out        :", len(body))
    print("BYTES IDENTICAL  :", body == original)
    print("token leaked?    :",
          blog.TELEGRAM_BOT_TOKEN in body.decode("utf-8", "ignore"))

    ok = (response.status_code == 200
          and body == original
          and response.headers.get("Content-Type", "").startswith("image/")
          and response.headers.get("Content-Disposition") == "inline")
    print("\nRESULT           :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

import hmac
import json
import mimetypes
import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps
from xml.sax.saxutils import escape

import requests as http
from dotenv import load_dotenv
from flask import (Flask, Response, flash, has_request_context, jsonify,
                   make_response, redirect, render_template, request, session,
                   url_for)
from flask_mail import Mail, Message
from pymongo import MongoClient

try:
    # Only needed when serving locally through waitress; Vercel supplies its own WSGI adapter.
    from waitress import serve
except ImportError:  # pragma: no cover
    serve = None

# Local development reads a .env file. On Vercel the variables come from the dashboard.
load_dotenv()

# Static assets live in public/ so Vercel's CDN can serve them. Pointing
# static_folder at the same directory keeps `python app.py` working locally.
app = Flask(__name__, static_folder='public/static', static_url_path='/static')

# ---------------------------------------------------------------------------
# Configuration (all values come from environment variables)
# ---------------------------------------------------------------------------
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

MONGO_URI = os.environ.get("MONGO_URI", "")
_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000) if MONGO_URI else None
db = _client.Blog if _client is not None else None

if db is None:
    # Not fatal at import time - robots.txt and the static pages still work -
    # but every database-backed route will fail. Say so loudly in the logs
    # rather than letting a bare `except` swallow the real cause.
    app.logger.error(
        "MONGO_URI is not set, so every database-backed page will fail. Set it "
        "in the environment: locally in .env, on Vercel under "
        "Project -> Settings -> Environment Variables."
    )

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USERNAME'] = os.environ.get("MAIL_USERNAME", "")
app.config['MAIL_PASSWORD'] = os.environ.get("MAIL_PASSWORD", "")
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = True
mail = Mail(app)

# Editing writes to the database and emails every subscriber, so it is gated
# behind a session login. Without credentials configured the routes fail closed
# instead of silently running unauthenticated.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Vercel terminates TLS, so only mark the cookie secure when running on HTTPS.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("VERCEL") == "1"

# ---------------------------------------------------------------------------
# Site metadata, used for canonical URLs, social previews and the sitemap.
# Set SITE_URL in production (e.g. https://rohadas.vercel.app) so those tags
# carry absolute URLs; without it they fall back to the request host.
# ---------------------------------------------------------------------------
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
SITE_NAME = "Rohadas Blog"
SITE_AUTHOR = "Chirag Rohada"
SITE_TAGLINE = "Travel, work and the things I build"
DEFAULT_DESCRIPTION = (
    "Rohadas Blog is the personal blog of Chirag Rohada - travel stories, "
    "photo journals and the projects he is building."
)
# Used for social previews when a post has no image of its own.
DEFAULT_OG_IMAGE = "dist/assets/img/favicon.png"
TWITTER_HANDLE = os.environ.get("TWITTER_HANDLE", "rohada_chirag")


def site_base():
    """Absolute site root, preferring the configured SITE_URL."""
    if SITE_URL:
        return SITE_URL
    if has_request_context():
        return request.url_root.rstrip("/")
    return ""


def absolute(path):
    """Turn a site-relative path into an absolute URL for tags that need one."""
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return site_base() + (path if path.startswith("/") else "/" + path)


def slugify(value):
    """URL-safe slug: 'Escaping the City!' -> 'escaping-the-city'."""
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").lower())
    return value.strip("-")[:80]


def unique_slug(category, desired, ignore_id=None):
    """A slug that is free in `category`, suffixed with -2, -3, ... if taken."""
    base = slugify(desired) or "post"
    candidate = base
    suffix = 2
    while True:
        existing = db[category].find_one({"url": candidate})
        if not existing or str(existing.get("_id")) == str(ignore_id):
            return candidate
        candidate = "%s-%d" % (base, suffix)
        suffix += 1


def og_image_url(post):
    """Absolute preview image for a post, falling back to the site icon."""
    for field in ("m-img", "img1"):
        if post.get(field):
            return absolute(media_url(post[field]))
    return absolute(url_for("static", filename=DEFAULT_OG_IMAGE))


def seo(title, description, path, image=None, kind="website", published=None):
    """The values every page's <head> needs."""
    return {
        "seo_title": title,
        "seo_description": description,
        "seo_canonical": absolute(path),
        "seo_image": image or absolute(url_for("static", filename=DEFAULT_OG_IMAGE)),
        "seo_type": kind,
        "article_published": published,
    }


def plain_text(value, limit=155):
    """Strip HTML and collapse whitespace, for meta descriptions."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return text

# Categories a post can live in. 'lifestyle' backs /travel, 'work' backs /work.
CATEGORIES = ("lifestyle", "work")


def credentials_configured():
    return bool(ADMIN_USERNAME and ADMIN_PASSWORD)


def is_admin():
    return session.get("admin") is True


def check_credentials(username, password):
    """Constant-time comparison so the check cannot be timed."""
    if not credentials_configured():
        return False
    return (hmac.compare_digest(username or "", ADMIN_USERNAME)
            and hmac.compare_digest(password or "", ADMIN_PASSWORD))


def requires_admin(view):
    """Send anonymous visitors to the login page."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not credentials_configured():
            return render_template(
                "error.html",
                heading="Admin Not Configured",
                message="Set ADMIN_USERNAME and ADMIN_PASSWORD to enable editing.",
            ), 503
        if not is_admin():
            return redirect(url_for("Login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def csrf_token():
    """Per-session token embedded in every admin form."""
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


@app.before_request
def require_csrf_on_posts():
    """Reject cross-site form posts to the routes that change data."""
    if request.method != "POST":
        return None
    if not (request.path.startswith("/admin") or request.path == "/add_blog"):
        return None
    sent = (request.form.get("_csrf", "")
            or request.headers.get("X-CSRF-Token", ""))
    expected = session.get("_csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        return render_template(
            "error.html",
            heading="Session Expired",
            message="Your editing session expired. Please sign in again and retry.",
        ), 400
    return None


@app.context_processor
def inject_site_metadata():
    """Values every template needs: admin state and site-wide metadata."""
    admin = is_admin()
    return {
        "is_admin": admin,
        # Only mint a token when a signed-in admin renders a form. Doing it for
        # anonymous visitors would set a session cookie on every page view and
        # stop the HTML being cached at the edge.
        "csrf_token": csrf_token if admin else (lambda: ""),
        "site_name": SITE_NAME,
        "site_author": SITE_AUTHOR,
        "site_tagline": SITE_TAGLINE,
        "site_url": site_base(),
        "twitter_handle": TWITTER_HANDLE,
        "default_description": DEFAULT_DESCRIPTION,
    }


@app.route("/login", methods=["GET", "POST"])
def Login():
    if not credentials_configured():
        return render_template(
            "error.html",
            heading="Admin Not Configured",
            message="Set ADMIN_USERNAME and ADMIN_PASSWORD to enable editing.",
        ), 503

    error = None
    if request.method == "POST":
        if check_credentials(request.form.get("username"),
                             request.form.get("password")):
            session.clear()
            session["admin"] = True
            session.permanent = False
            target = request.args.get("next") or url_for("AdminHome")
            # Only ever redirect to a local path.
            if not target.startswith("/"):
                target = url_for("AdminHome")
            return redirect(target)
        error = "Incorrect username or password."

    return render_template("admin_login.html", error=error), (
        401 if error else 200)


@app.route("/logout")
def Logout():
    session.clear()
    return redirect(url_for("Home"))


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def get_post(category, url):
    if category not in CATEGORIES:
        return None
    return db[category].find_one({"url": url})


def post_paragraphs(post):
    """body1 is stored as a newline-separated string of paragraphs."""
    if not post:
        return [""]
    raw = post.get("body1") or ""
    return raw.split("\n") if raw else [""]


def blocks_from_post(post):
    """Body content as an ordered list of blocks.

    Posts written before the inline editor have no `blocks` field, so their
    paragraphs and images are assembled in the order the old template rendered
    them. That keeps every existing post looking exactly as it did.
    """
    stored = post.get("blocks")
    if stored:
        return list(stored)

    blocks = []
    if post.get("img1"):
        blocks.append({"type": "image", "ref": post["img1"]})
    for paragraph in post_paragraphs(post):
        if paragraph.strip():
            blocks.append({"type": "text", "html": paragraph})
    if (post.get("body2") or "").strip():
        blocks.append({"type": "text", "html": post["body2"]})
    if post.get("img2"):
        blocks.append({"type": "image", "ref": post["img2"]})
    return blocks


# Guard rails for content coming from the browser.
MAX_BLOCKS = 300


def clean_blocks(raw):
    """Validate blocks posted by the inline editor before storing them."""
    if not isinstance(raw, list):
        raise ValueError("Content must be a list of blocks.")

    blocks = []
    for item in raw[:MAX_BLOCKS]:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            html = (item.get("html") or "").strip()
            if html:
                blocks.append({"type": "text", "html": html})
        elif kind == "image":
            ref = (item.get("ref") or "").strip()
            if ref:
                blocks.append({"type": "image", "ref": ref})
    return blocks


def _apply_form(post, is_new):
    """Copy submitted fields onto `post`, uploading any replacement images.

    Existing values are left untouched when no new file is supplied, because
    `post` already carries them.
    """
    post["heading"] = request.form.get("heading", "").strip()
    post["url"] = request.form.get("url", "").strip()
    post["heading2"] = request.form.get("heading2", "").strip()
    post["discription"] = request.form.get("discription", "")
    post["body2"] = request.form.get("body2", "")
    post["body1"] = "\n".join(request.form.getlist("body1"))

    raw_date = request.form.get("date", "").strip()
    if raw_date:
        post["date"] = datetime.strptime(raw_date, "%Y-%m-%d")
    elif is_new:
        post["date"] = datetime.now()

    for field, input_name in (("m-img", "m_img"), ("img1", "img1"),
                              ("img2", "img2")):
        uploaded = request.files.get(input_name)
        if uploaded and uploaded.filename:
            post[field] = telegram_upload(uploaded)
        elif request.form.get("clear_" + input_name) == "1":
            post[field] = None
    return post


def _render_editor(category, post, error=None, is_new=False):
    return render_template(
        "admin_edit.html",
        category=category,
        post=post,
        paragraphs=post_paragraphs(post),
        categories=CATEGORIES,
        error=error,
        is_new=is_new,
    )


@app.route("/admin")
@requires_admin
def AdminHome():
    groups = [(category, list(db[category].find().sort([("date", -1)])))
              for category in CATEGORIES]
    return render_template("admin_dashboard.html", groups=groups)


@app.route("/admin/new")
@app.route("/admin/new/<category>")
@requires_admin
def AdminNew(category="lifestyle"):
    """Create an empty post and open it straight in the inline editor.

    Deliberately form-free: the post page *is* the editor, so you land on a
    blank post, click the title and start writing.
    """
    if category not in CATEGORIES:
        return render_template(
            "error.html", heading="Unknown Category",
            message="A post must belong to one of: %s." % ", ".join(CATEGORIES),
        ), 404

    slug = unique_slug(category, "untitled")
    db[category].insert_one({
        "heading": "Untitled post",
        "url": slug,
        "date": datetime.now(),
        "discription": "",
        "heading2": "",
        "body1": "",
        "blocks": [{"type": "text", "html": "<p>Start writing&hellip;</p>"}],
        "m-img": None,
    })
    return redirect(url_for("Blog", url=slug, edit=1))


@app.route("/admin/edit/<category>/<url>", methods=["GET", "POST"])
@requires_admin
def AdminEdit(category, url):
    if category not in CATEGORIES:
        return render_template(
            "error.html", heading="Unknown Category",
            message="That category does not exist."), 404

    post = get_post(category, url)
    if not post:
        return render_template(
            "error.html", heading="Post Not Found",
            message="That post does not exist or has been removed."), 404

    if request.method == "POST":
        try:
            _apply_form(post, is_new=False)
            if not post.get("heading") or not post.get("url"):
                raise ValueError("A title and a URL slug are both required.")

            new_url = post["url"]
            if new_url != url and db[category].find_one({"url": new_url}):
                raise ValueError("The URL slug '%s' is already taken." % new_url)

            db[category].replace_one({"_id": post["_id"]}, post)
            return redirect(url_for("Blog", url=new_url))
        except Exception as exc:
            return _render_editor(category, post, error=str(exc))

    return _render_editor(category, post)


@app.route("/admin/delete/<category>/<url>", methods=["POST"])
@requires_admin
def AdminDelete(category, url):
    if category not in CATEGORIES or not get_post(category, url):
        return render_template(
            "error.html", heading="Post Not Found",
            message="That post does not exist or has been removed."), 404
    db[category].delete_one({"url": url})
    return redirect(url_for("AdminHome"))


@app.route("/admin/move/<category>/<url>/<direction>", methods=["POST"])
@requires_admin
def AdminMove(category, url, direction):
    """Move a post earlier or later in its category's date ordering."""
    if direction not in ("up", "down"):
        return render_template(
            "error.html", heading="Unknown Direction",
            message="Use 'up' or 'down'."), 400

    post = get_post(category, url)
    if not post:
        return render_template(
            "error.html", heading="Post Not Found",
            message="That post does not exist or has been removed."), 404

    ordered = list(db[category].find().sort([("date", -1)]))
    index = next((i for i, p in enumerate(ordered)
                  if str(p["_id"]) == str(post["_id"])), None)
    if index is None:
        return redirect(url_for("AdminHome"))

    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(ordered):
        other = ordered[target]
        mine, theirs = post.get("date"), other.get("date")
        if mine == theirs:
            # Identical timestamps cannot swap, so nudge one by a second.
            theirs = theirs + timedelta(seconds=1)
        db[category].update_one({"_id": post["_id"]}, {"$set": {"date": theirs}})
        db[category].update_one({"_id": other["_id"]}, {"$set": {"date": mine}})

    return redirect(url_for("AdminHome"))


# ---------------------------------------------------------------------------
# Inline editor endpoints (called by fetch() from the post page)
# ---------------------------------------------------------------------------


@app.route("/admin/upload", methods=["POST"])
@requires_admin
def AdminUpload():
    """Accept an image from the inline editor and return its stored reference."""
    image = request.files.get("image")
    if not image or not image.filename:
        return jsonify({"ok": False, "error": "No image was uploaded."}), 400
    try:
        reference = telegram_upload(image)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    key = reference[len(TG_PREFIX):]
    return jsonify({"ok": True, "ref": reference,
                    "url": url_for("Media", key=key)})


@app.route("/admin/save/<category>/<url>", methods=["POST"])
@requires_admin
def AdminSave(category, url):
    """Save edits made directly on the post page."""
    if category not in CATEGORIES:
        return jsonify({"ok": False, "error": "Unknown category."}), 404

    post = get_post(category, url)
    if not post:
        return jsonify({"ok": False, "error": "Post not found."}), 404

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Expected a JSON body."}), 400

    try:
        if "heading" in payload:
            heading = (payload["heading"] or "").strip()
            if not heading:
                raise ValueError("The title cannot be empty.")
            post["heading"] = heading
        if "discription" in payload:
            post["discription"] = payload["discription"] or ""
        if "heading2" in payload:
            post["heading2"] = (payload["heading2"] or "").strip()
        if "blocks" in payload:
            post["blocks"] = clean_blocks(payload["blocks"])

        # Posts created from the inline editor start life as 'untitled-*'. Once
        # one has a real title, give it a matching URL.
        if post["url"].startswith("untitled") and post.get("heading"):
            candidate = slugify(post["heading"])
            if candidate:
                post["url"] = unique_slug(category, candidate,
                                          ignore_id=post["_id"])

        db[category].replace_one({"_id": post["_id"]}, post)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True,
                    "saved_at": datetime.now().strftime("%H:%M:%S"),
                    "url": post["url"]})

# ---------------------------------------------------------------------------
# Telegram is used as the image store, because Vercel's filesystem is read-only.
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org/bot%s/%s"
TELEGRAM_FILE = "https://api.telegram.org/file/bot%s/%s"

# Marks a stored value as a Telegram file_id instead of a legacy filename stem
# that lives in public/static/images/post_images/.
TG_PREFIX = "tg:"

# Vercel caps a function's request *and* response body at 4.5 MB, which is a far
# tighter bound than Telegram's own 20 MB getFile limit. Uploads are rejected
# here with a clear message instead of failing mid-request with a 413, and the
# same ceiling applies to anything streamed back out of /media.
VERCEL_BODY_LIMIT = 4.5 * 1024 * 1024
MAX_UPLOAD_BYTES = 4 * 1024 * 1024

# Telegram serves every stored document as application/octet-stream with
# Content-Disposition: attachment, so its headers cannot be trusted to decide
# how a browser should render an image. Detect the type from magic bytes.
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_type(blob):
    """Return the MIME type implied by an image's magic bytes, or None."""
    for signature, mime in IMAGE_SIGNATURES:
        if blob.startswith(signature):
            return mime
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    return None


def telegram_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def telegram_upload(file_storage):
    """Store an uploaded image in Telegram and return a 'tg:<file_id>' reference.

    sendDocument is used rather than sendPhoto so Telegram keeps the original
    bytes instead of re-compressing the image.
    """
    if file_storage is None or not file_storage.filename:
        return None
    if not telegram_enabled():
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not configured")

    blob = file_storage.read()
    if not blob:
        return None
    if len(blob) > MAX_UPLOAD_BYTES:
        raise ValueError("Image exceeds the 4 MB limit imposed by Vercel Functions")

    response = http.post(
        TELEGRAM_API % (TELEGRAM_BOT_TOKEN, "sendDocument"),
        data={"chat_id": TELEGRAM_CHAT_ID},
        files={"document": (file_storage.filename, blob,
                            file_storage.mimetype or "application/octet-stream")},
        timeout=60,
    )
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram rejected the upload: %s" % payload.get("description"))
    document = payload.get("result", {}).get("document")
    if not document:
        raise RuntimeError("Telegram response contained no document")
    return TG_PREFIX + document["file_id"]


_legacy_filename_cache = {}


def legacy_filename(stem):
    """Return the real filename on disk for a legacy image stem.

    Older posts store a filename stem and the templates appended '.jpg', but a
    few files have no extension or a different one, which produced 404s. The
    lookup is cached because it happens for every image on every page render.
    """
    if stem in _legacy_filename_cache:
        return _legacy_filename_cache[stem]

    folder = os.path.join(app.static_folder or "", "images", "post_images")
    resolved = "%s.jpg" % stem
    try:
        candidates = sorted(
            name for name in os.listdir(folder)
            if os.path.splitext(name)[0] == stem or name.startswith(stem + ".")
        )
    except OSError:
        candidates = []

    if candidates:
        # Prefer a file whose name matches the stem exactly, e.g. 'a.jpg' over
        # 'a..png'. Sorting first keeps the choice deterministic.
        exact = [n for n in candidates if os.path.splitext(n)[0] == stem]
        resolved = (exact or candidates)[0]

    _legacy_filename_cache[stem] = resolved
    return resolved


@app.template_global()
def media_url(value):
    """Resolve a stored image reference into a URL.

    New posts hold 'tg:<file_id>' and are streamed through /media/<file_id>.
    Posts created before the migration hold a bare filename stem and are served
    straight from public/static/images/post_images/.
    """
    if not value:
        return ""
    value = str(value)
    if value.startswith(TG_PREFIX):
        return url_for("Media", key=value[len(TG_PREFIX):])
    return url_for("static",
                   filename="images/post_images/%s" % legacy_filename(value))


@app.route('/media/<path:key>')
def Media(key):
    """Proxy a Telegram-hosted image.

    The bot token must never reach the browser, so the bytes are streamed
    through here rather than redirecting to api.telegram.org. The long
    Cache-Control header lets Vercel's CDN absorb repeat views.
    """
    if not telegram_enabled():
        return render_template("error.html"), 404
    try:
        info = http.get(TELEGRAM_API % (TELEGRAM_BOT_TOKEN, "getFile"),
                        params={"file_id": key}, timeout=20).json()
        if not info.get("ok"):
            return render_template("error.html"), 404
        file_path = info["result"]["file_path"]

        download = http.get(TELEGRAM_FILE % (TELEGRAM_BOT_TOKEN, file_path), timeout=30)
        download.raise_for_status()

        blob = download.content

        telegram_type = download.headers.get("Content-Type")
        if telegram_type == "application/octet-stream":
            telegram_type = None

        content_type = (sniff_image_type(blob)
                        or mimetypes.guess_type(file_path)[0]
                        or telegram_type
                        or "image/jpeg")

        response = make_response(blob)
        response.headers["Content-Type"] = content_type
        # Telegram sends Content-Disposition: attachment; force inline so the
        # browser renders the image instead of downloading it.
        response.headers["Content-Disposition"] = "inline"
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
    except Exception:
        return render_template("error.html"), 404


@app.route('/')
def Home():
    try:
        data = db['lifestyle'].find().sort([("date", -1)]).limit(3)
        data2 = db['work'].find().sort([("date", -1)]).limit(3)

        return render_template(
            'index.html', data=data, data2=data2,
            **seo("%s | %s" % (SITE_NAME, SITE_TAGLINE),
                  DEFAULT_DESCRIPTION, "/"))
    except Exception:
        # This used to `return "Error"` - a five byte body that told the visitor
        # nothing and left no traceback in the logs. Log it and render a real
        # page, with a 500 so crawlers do not index the failure.
        app.logger.exception("Could not render the home page")
        return render_template(
            "error.html",
            heading="Something Went Wrong",
            message="This page could not be loaded. Please try again in a moment.",
        ), 500


@app.route('/about_us')
def About():

    return render_template(
        'about_us.html',
        **seo("About | %s" % SITE_NAME,
              "About Rohadas Blog - the personal blog of Chirag Rohada, an "
              "entrepreneur who writes about travel, work and the things he "
              "is building.",
              "/about_us"))


@app.route('/add_blog', methods=["GET", "POST"])
@requires_admin
def Add_blog():

    if request.method == "POST":
        try:
            if not telegram_enabled():
                return render_template(
                    "error.html",
                    heading="Image Storage Not Configured",
                    message="Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before publishing.",
                ), 503

            heading = request.form.get("heading")
            discription = request.form.get("discription")
            body1 = request.form.get("body1")
            body2 = request.form.get("body2")
            heading2 = request.form.get("heading2")
            url = request.form.get("url")
            info = request.form.get("info")
            date = request.form.get("date")

            # Images are pushed to Telegram; nothing is written to disk because
            # Vercel's filesystem is read-only.
            main_ref = telegram_upload(request.files.get("m_img"))
            img1_ref = telegram_upload(request.files.get("img1"))

            mycollection = db[info]
            date_object = datetime.strptime(date, '%Y-%m-%d')
            mydict = {"heading": heading, "url": url, "discription": discription,
                      "date": date_object, "m-img": main_ref, "img1": img1_ref,
                      "body1": body1, "body2": body2, "heading2": heading2}

            mycollection.insert_one(mydict)

            # The post is already stored, so a mail failure must not lose it.
            try:
                mycollection = db["email"]
                email_list = list(mycollection.distinct("email"))
                msg = Message(
                    subject="Rohadas Blog",
                    sender=app.config['MAIL_USERNAME'],
                    recipients=email_list
                )
                msg.html = render_template('email.html', heading=heading,
                                           discription=discription, url=url)
                mail.send(msg)
            except Exception:
                pass

            return render_template('add_detail.html')
        except Exception:
            return render_template(
                "error.html",
                heading="Could Not Publish",
                message="The post was not saved. Check the required fields and try again.",
            ), 500

    return render_template('add_detail.html')


@app.route('/search/<input>')
def Search(input):
    mycollection = db["lifestyle"]

    result = mycollection.find({'$text': {'$search': input}})
    list = []
    for i in result:

        list.append({"heading": i['heading'], "url": i['url']})
    mycollection = db["work"]

    result = mycollection.find({'$text': {'$search': input}})
    for i in result:

        list.append({"heading": i['heading'], "url": i['url']})
    print(list)

    return list


@app.route('/travel')
def Travel():
    try:
        mycollection = db["lifestyle"]
        data = mycollection.find()
        return render_template(
            'travel.html', data=data,
            **seo("Travel | %s" % SITE_NAME,
                  "Travel stories and photo journals from Chirag Rohada - the "
                  "places he has been and what he learned along the way.",
                  "/travel"))
    except Exception:
        # A silent except here renders the generic "Page Not Found" template,
        # which is actively misleading when the real cause is a database
        # failure. Log the traceback and say something accurate.
        app.logger.exception("Could not render the travel index")
        return render_template(
            "error.html",
            heading="Something Went Wrong",
            message="The travel posts could not be loaded. Please try again in a moment.",
        ), 500


@app.route('/work')
def Work():
    try:
        mycollection = db["work"]
        data = mycollection.find()
        return render_template(
            'work.html', data=data,
            **seo("Work | %s" % SITE_NAME,
                  "Projects, products and experiments built by Chirag Rohada.",
                  "/work"))
    except Exception:
        # See the note on the travel index above.
        app.logger.exception("Could not render the work index")
        return render_template(
            "error.html",
            heading="Something Went Wrong",
            message="The work posts could not be loaded. Please try again in a moment.",
        ), 500


@app.route('/email_subscribe', methods=["GET", "POST"])
def Email():
    try:

        if request.method == "POST":
            email = request.form.get("email")
            mycollection = db["email"]

            mycollection.insert_one({"email": email})
            flash('You were successfully logged in')
            return redirect(url_for('Home'))
    except Exception:
        return render_template("error.html")


@app.route('/blog/<url>')
def Blog(url):
    try:
        category = "lifestyle"
        data = db[category].find_one({'url': url})

        if not data:
            category = "work"
            data = db[category].find_one({'url': url})

        if not data:
            return render_template(
                "error.html",
                heading="Post Not Found",
                message="That post does not exist or has been removed.",
            ), 404

        # blocks_from_post handles the stored body1 string (and every legacy
        # post) itself, so no pre-splitting happens here.
        post_blocks = blocks_from_post(data)
        return render_template(
            'blog.html', data=data, category=category, blocks=post_blocks,
            **seo("%s | %s" % (data.get("heading", "Post"), SITE_NAME),
                  plain_text(data.get("discription")) or DEFAULT_DESCRIPTION,
                  "/blog/" + data["url"],
                  image=og_image_url(data),
                  kind="article",
                  published=(data["date"].isoformat()
                             if data.get("date") else None)))
    except Exception:
        # Log the traceback: a bare except here once hid a real rendering bug
        # behind a generic "Page Not Found".
        app.logger.exception("Could not render post %r", url)
        return render_template('error.html')


# ---------------------------------------------------------------------------
# Crawler entry points
# ---------------------------------------------------------------------------


@app.route("/robots.txt")
def Robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        # Everything behind a login, or that changes data.
        "Disallow: /admin",
        "Disallow: /admin/",
        "Disallow: /login",
        "Disallow: /logout",
        "Disallow: /add_blog",
        "",
        "Sitemap: %s/sitemap.xml" % site_base(),
        "",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/sitemap.xml")
def Sitemap():
    entries = [
        ("/", None, "1.0", "daily"),
        ("/travel", None, "0.8", "weekly"),
        ("/work", None, "0.8", "weekly"),
        ("/about_us", None, "0.5", "monthly"),
    ]

    try:
        for category in CATEGORIES:
            for post in db[category].find():
                if post.get("url"):
                    entries.append(("/blog/" + post["url"], post.get("date"),
                                    "0.9", "monthly"))
    except Exception:
        app.logger.exception("Could not build the sitemap from the database")

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, lastmod, priority, changefreq in entries:
        parts.append("  <url>")
        parts.append("    <loc>%s</loc>" % escape(absolute(path)))
        if lastmod:
            parts.append("    <lastmod>%s</lastmod>"
                         % lastmod.strftime("%Y-%m-%d"))
        parts.append("    <changefreq>%s</changefreq>" % changefreq)
        parts.append("    <priority>%s</priority>" % priority)
        parts.append("  </url>")
    parts.append("</urlset>")

    return Response("\n".join(parts), mimetype="application/xml")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG", "0") == "1")

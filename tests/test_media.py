"""Tests for the Telegram-backed image storage that replaces disk writes.

Vercel's filesystem is read-only, so uploaded images are pushed to Telegram and
served back through /media/<file_id>. These tests stub out the Telegram HTTP
calls so no bot token is required.

Run with:  python -m unittest discover -s tests -v
"""

import io
import unittest
from datetime import datetime
from unittest import mock

from werkzeug.datastructures import FileStorage

import app as blog


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, payload=None, content=b"", headers=None, status=200):
        self._payload = payload
        self.content = content
        self.headers = headers or {}
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %s" % self.status_code)


def upload(data=b"\xff\xd8\xffimage", filename="photo.jpg"):
    return FileStorage(stream=io.BytesIO(data), filename=filename,
                       content_type="image/jpeg")


# Minimal payloads carrying correct magic bytes.
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"jpeg-payload"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"png-payload"


# ---------------------------------------------------------------------------
# A stand-in for MongoDB so the suite never touches the network. Talking to
# Atlas made these tests slow (a cold connect costs seconds) and flaky.
# ---------------------------------------------------------------------------

SAMPLE_POST = {
    "_id": "sample-id",
    "heading": "Sample Post",
    "url": "sample-post",
    "date": datetime(2026, 1, 1),
    "m-img": "tg:SAMPLE",
    "body1": "one paragraph",
}


class FakeCursor(list):
    """Mimics enough of a PyMongo cursor to satisfy the app."""

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    def find(self, filter=None, *args, **kwargs):
        if not filter:
            return FakeCursor(self.documents)
        return FakeCursor(
            doc for doc in self.documents
            if all(doc.get(k) == v for k, v in filter.items())
        )

    def find_one(self, filter=None, *args, **kwargs):
        if not filter:
            return self.documents[0] if self.documents else None
        for doc in self.documents:
            if all(doc.get(k) == v for k, v in filter.items()):
                return doc
        return None

    def distinct(self, *args, **kwargs):
        return []

    def insert_one(self, *args, **kwargs):
        return None

    def delete_one(self, *args, **kwargs):
        return None

    def update_one(self, *args, **kwargs):
        return None

    def replace_one(self, *args, **kwargs):
        return None


class FakeDb:
    def __init__(self, documents=None):
        self.documents = list(documents) if documents is not None \
            else [dict(SAMPLE_POST)]

    def __getitem__(self, name):
        return FakeCollection(self.documents)


def use_fake_db(test_case, documents=None):
    """Point the app at the in-memory stand-in for the duration of a test."""
    patcher = mock.patch.object(blog, "db", FakeDb(documents))
    patcher.start()
    test_case.addCleanup(patcher.stop)


def use_admin_credentials(test_case, username="admin", password="secret"):
    for name, value in (("ADMIN_USERNAME", username),
                        ("ADMIN_PASSWORD", password)):
        patcher = mock.patch.object(blog, name, value)
        patcher.start()
        test_case.addCleanup(patcher.stop)



class UploadTests(unittest.TestCase):

    def test_returns_tg_prefixed_file_id(self):
        response = FakeResponse({"ok": True, "result": {"document": {"file_id": "ABC123"}}})
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", "TOKEN"), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"), \
             mock.patch.object(blog.http, "post", return_value=response) as post:
            self.assertEqual(blog.telegram_upload(upload()), "tg:ABC123")

        # sendDocument is used so Telegram keeps the original bytes.
        self.assertIn("sendDocument", post.call_args[0][0])
        self.assertEqual(post.call_args[1]["data"]["chat_id"], "42")

    def test_blank_upload_returns_none(self):
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", "TOKEN"), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"):
            self.assertIsNone(blog.telegram_upload(FileStorage(filename="")))
            self.assertIsNone(blog.telegram_upload(upload(b"")))

    def test_raises_when_not_configured(self):
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", ""), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", ""):
            with self.assertRaises(RuntimeError):
                blog.telegram_upload(upload())

    def test_raises_on_oversized_image(self):
        oversize = b"x" * (blog.MAX_UPLOAD_BYTES + 1)
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", "TOKEN"), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"):
            with self.assertRaises(ValueError):
                blog.telegram_upload(upload(oversize))

    def test_raises_when_telegram_rejects(self):
        response = FakeResponse({"ok": False, "description": "chat not found"})
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", "TOKEN"), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"), \
             mock.patch.object(blog.http, "post", return_value=response):
            with self.assertRaises(RuntimeError):
                blog.telegram_upload(upload())


class MediaUrlTests(unittest.TestCase):

    def test_telegram_reference_maps_to_media_route(self):
        with blog.app.test_request_context():
            self.assertEqual(blog.media_url("tg:ABC123"), "/media/ABC123")

    def test_legacy_stem_maps_to_static_file(self):
        with blog.app.test_request_context():
            self.assertEqual(blog.media_url("matheran1"),
                             "/static/images/post_images/matheran1.jpg")

    def test_empty_value(self):
        with blog.app.test_request_context():
            self.assertEqual(blog.media_url(None), "")
            self.assertEqual(blog.media_url(""), "")


class SniffImageTypeTests(unittest.TestCase):

    def test_jpeg(self):
        self.assertEqual(blog.sniff_image_type(JPEG_BYTES), "image/jpeg")

    def test_png(self):
        self.assertEqual(blog.sniff_image_type(PNG_BYTES), "image/png")

    def test_gif(self):
        self.assertEqual(blog.sniff_image_type(b"GIF89a...."), "image/gif")

    def test_webp(self):
        self.assertEqual(blog.sniff_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 "),
                         "image/webp")

    def test_unknown_returns_none(self):
        self.assertIsNone(blog.sniff_image_type(b"not an image"))
        self.assertIsNone(blog.sniff_image_type(b""))


class MediaRouteTests(unittest.TestCase):

    def setUp(self):
        self.client = blog.app.test_client()
        self.token = "SECRET-BOT-TOKEN"

    def _telegram_calls(self, blob=JPEG_BYTES, file_path="documents/file_1.jpg"):
        """getFile response, then the file download.

        Telegram really answers with application/octet-stream plus
        Content-Disposition: attachment, so the fixture reproduces that instead
        of an idealised image/jpeg response.
        """
        return [
            FakeResponse({"ok": True, "result": {"file_path": file_path}}),
            FakeResponse(content=blob,
                         headers={"Content-Type": "application/octet-stream"}),
        ]

    def test_streams_bytes_with_long_cache(self):
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", self.token), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"), \
             mock.patch.object(blog.http, "get", side_effect=self._telegram_calls()):
            response = self.client.get("/media/ABC123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, JPEG_BYTES)
        # Caching at the edge is what keeps repeat views off the function.
        self.assertIn("public", response.headers["Cache-Control"])
        self.assertIn("immutable", response.headers["Cache-Control"])

    def test_octet_stream_is_replaced_with_a_renderable_type(self):
        """Telegram's own Content-Type would make browsers download the image."""
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", self.token), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"), \
             mock.patch.object(blog.http, "get", side_effect=self._telegram_calls()):
            response = self.client.get("/media/ABC123")

        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertEqual(response.headers["Content-Disposition"], "inline")

    def test_png_detected_even_without_a_file_extension(self):
        calls = self._telegram_calls(blob=PNG_BYTES, file_path="documents/file_1")
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", self.token), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"), \
             mock.patch.object(blog.http, "get", side_effect=calls):
            response = self.client.get("/media/ABC123")

        self.assertEqual(response.headers["Content-Type"], "image/png")

    def test_never_exposes_the_bot_token(self):
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", self.token), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"), \
             mock.patch.object(blog.http, "get", side_effect=self._telegram_calls()):
            response = self.client.get("/media/ABC123")

        self.assertNotIn(self.token, response.data.decode("utf-8", "ignore"))
        self.assertNotIn(self.token, str(dict(response.headers)))

    def test_missing_file_returns_404(self):
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", self.token), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"), \
             mock.patch.object(blog.http, "get",
                               return_value=FakeResponse({"ok": False})):
            self.assertEqual(self.client.get("/media/nope").status_code, 404)

    def test_404_when_telegram_not_configured(self):
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", ""), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", ""):
            self.assertEqual(self.client.get("/media/ABC123").status_code, 404)

    def test_telegram_outage_is_handled(self):
        with mock.patch.object(blog, "TELEGRAM_BOT_TOKEN", self.token), \
             mock.patch.object(blog, "TELEGRAM_CHAT_ID", "42"), \
             mock.patch.object(blog.http, "get", side_effect=RuntimeError("timeout")):
            self.assertEqual(self.client.get("/media/ABC123").status_code, 404)


class TemplateIntegrationTests(unittest.TestCase):
    """Prove image references render as real <img> tags on the post page."""

    def render(self, post, category="lifestyle"):
        from flask import render_template

        with blog.app.test_request_context():
            return render_template("blog.html", data=post, category=category,
                                   blocks=blog.blocks_from_post(post))

    def test_telegram_image_renders_through_the_media_route(self):
        html = self.render({
            "heading": "Test Post",
            "date": datetime(2026, 1, 1),
            "url": "test-post",
            "img1": "tg:FILEID123",
            "discription": "desc",
            "body1": "paragraph one",
        })
        self.assertIn('src="/media/FILEID123"', html)

    def test_legacy_static_image_still_renders(self):
        html = self.render({
            "heading": "Old Post",
            "date": datetime(2026, 1, 1),
            "url": "old-post",
            "img1": "matheran1",
            "discription": "desc",
            "body1": "paragraph one",
        })
        self.assertIn('src="/static/images/post_images/matheran1.jpg"', html)

    def test_the_main_image_is_used_as_the_cover(self):
        html = self.render({
            "heading": "Cover Post",
            "date": datetime(2026, 1, 1),
            "url": "cover-post",
            "m-img": "tg:COVER",
            "discription": "desc",
            "body1": "text",
        })
        self.assertIn('src="/media/COVER"', html)

    def test_stored_blocks_render_in_their_stored_order(self):
        html = self.render({
            "heading": "Blocks",
            "date": datetime(2026, 1, 1),
            "url": "blocks",
            "blocks": [
                {"type": "text", "html": "first para"},
                {"type": "image", "ref": "tg:IMG"},
                {"type": "text", "html": "last para"},
            ],
        })
        self.assertLess(html.index("first para"), html.index("tg:IMG"))
        self.assertLess(html.index("tg:IMG"), html.index("last para"))

    def test_the_inline_editor_is_hidden_from_visitors(self):
        html = self.render({
            "heading": "Post", "date": datetime(2026, 1, 1),
            "url": "post", "body1": "text",
        })
        self.assertNotIn("inline-toggle", html)
        self.assertNotIn("POST_EDITOR", html)


class AdminAuthTests(unittest.TestCase):
    """Editing writes to the database, so it is gated behind a session login."""

    def setUp(self):
        self.client = blog.app.test_client()
        use_fake_db(self)
        use_admin_credentials(self)

    def login(self, username="admin", password="secret"):
        return self.client.post("/login",
                                data={"username": username,
                                      "password": password})

    def logged_in(self):
        with self.client.session_transaction() as sess:
            return sess.get("admin") is True

    def test_refuses_to_serve_when_credentials_are_unset(self):
        with mock.patch.object(blog, "ADMIN_USERNAME", ""), \
             mock.patch.object(blog, "ADMIN_PASSWORD", ""):
            self.assertEqual(self.client.get("/admin").status_code, 503)

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_login_rejects_a_wrong_password(self):
        response = self.login(password="nope")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.logged_in())

    def test_login_accepts_the_right_password(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.logged_in())

    def test_admin_pages_are_reachable_after_login(self):
        self.login()
        self.assertEqual(self.client.get("/admin").status_code, 200)

    def test_logout_clears_the_session(self):
        self.login()
        self.client.get("/logout")
        self.assertFalse(self.logged_in())

    def test_post_without_a_csrf_token_is_rejected(self):
        self.login()
        response = self.client.post("/admin/delete/lifestyle/anything", data={})
        self.assertEqual(response.status_code, 400)

    def test_anonymous_visitors_get_no_session_cookie(self):
        """Keeps public HTML cacheable at the edge."""
        self.assertNotIn("Set-Cookie", self.client.get("/").headers)

    def test_public_routes_stay_public(self):
        for path in ("/", "/about_us", "/travel", "/work"):
            self.assertNotIn(self.client.get(path).status_code, (401, 403),
                             "%s should not require auth" % path)

    def test_a_csrf_token_is_issued_when_an_admin_renders_a_form(self):
        self.login()
        self.client.get("/admin")  # the dashboard renders forms
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("_csrf"))


class PostParagraphTests(unittest.TestCase):
    """body1 is stored as one newline-joined string."""

    def test_splits_stored_newlines_into_paragraphs(self):
        self.assertEqual(blog.post_paragraphs({"body1": "one\ntwo\nthree"}),
                         ["one", "two", "three"])

    def test_missing_body_gives_one_empty_box(self):
        self.assertEqual(blog.post_paragraphs({}), [""])
        self.assertEqual(blog.post_paragraphs(None), [""])

    def test_blank_body_gives_one_empty_box(self):
        self.assertEqual(blog.post_paragraphs({"body1": ""}), [""])


class LegacyFilenameTests(unittest.TestCase):
    """Legacy posts store a bare stem; the real file may have another extension."""

    def test_finds_plain_jpg(self):
        self.assertEqual(blog.legacy_filename("matheran1"), "matheran1.jpg")

    def test_finds_file_without_any_extension(self):
        self.assertEqual(blog.legacy_filename("taarit_api_1"), "taarit_api_1")

    def test_finds_png_with_a_double_dot(self):
        resolved = blog.legacy_filename("Fundlet")
        self.assertTrue(resolved.startswith("Fundlet."), resolved)
        self.assertTrue(resolved.endswith(".png"), resolved)

    def test_unknown_stem_falls_back_to_jpg(self):
        self.assertEqual(blog.legacy_filename("no-such-file"),
                         "no-such-file.jpg")

    def test_media_url_uses_the_resolved_name(self):
        with blog.app.test_request_context():
            self.assertEqual(blog.media_url("taarit_api_1"),
                             "/static/images/post_images/taarit_api_1")


class BlockTests(unittest.TestCase):
    """The inline editor stores body content as an ordered list of blocks."""

    def test_legacy_post_is_converted_in_the_old_render_order(self):
        post = {
            "img1": "tg:FIRST",
            "body1": "para one\npara two",
            "body2": "closing",
            "img2": "tg:SECOND",
        }
        self.assertEqual(blog.blocks_from_post(post), [
            {"type": "image", "ref": "tg:FIRST"},
            {"type": "text", "html": "para one"},
            {"type": "text", "html": "para two"},
            {"type": "text", "html": "closing"},
            {"type": "image", "ref": "tg:SECOND"},
        ])

    def test_stored_blocks_take_precedence(self):
        stored = [{"type": "text", "html": "new"}]
        self.assertEqual(
            blog.blocks_from_post({"blocks": stored, "body1": "old"}), stored)

    def test_a_post_with_nothing_in_it_has_no_blocks(self):
        self.assertEqual(blog.blocks_from_post({}), [])

    def test_clean_blocks_keeps_only_usable_entries(self):
        cleaned = blog.clean_blocks([
            {"type": "text", "html": "  keep  "},
            {"type": "image", "ref": "tg:ABC"},
            {"type": "text", "html": "   "},
            {"type": "image", "ref": ""},
            {"type": "bogus"},
            "not a dict",
        ])
        self.assertEqual(cleaned, [
            {"type": "text", "html": "keep"},
            {"type": "image", "ref": "tg:ABC"},
        ])

    def test_clean_blocks_rejects_a_non_list(self):
        with self.assertRaises(ValueError):
            blog.clean_blocks({"type": "text"})

    def test_clean_blocks_caps_the_block_count(self):
        many = [{"type": "text", "html": "x"}] * (blog.MAX_BLOCKS + 50)
        self.assertEqual(len(blog.clean_blocks(many)), blog.MAX_BLOCKS)


class InlineEditorEndpointTests(unittest.TestCase):

    def setUp(self):
        self.client = blog.app.test_client()
        use_fake_db(self)
        use_admin_credentials(self)

    def login_and_get_token(self):
        self.client.post("/login",
                         data={"username": "admin", "password": "secret"})
        self.client.get("/admin")  # renders forms, which mints the token
        with self.client.session_transaction() as sess:
            return sess.get("_csrf")

    def test_save_is_refused_without_a_session(self):
        response = self.client.post("/admin/save/lifestyle/matheran-1",
                                    json={"blocks": []})
        self.assertIn(response.status_code, (400, 302))

    def test_save_is_refused_without_a_csrf_token(self):
        self.login_and_get_token()
        response = self.client.post("/admin/save/lifestyle/matheran-1",
                                    json={"blocks": []})
        self.assertEqual(response.status_code, 400)

    def test_save_rejects_an_unknown_post(self):
        token = self.login_and_get_token()
        response = self.client.post("/admin/save/lifestyle/does-not-exist",
                                    json={"blocks": []},
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 404)

    def test_save_rejects_an_unknown_category(self):
        token = self.login_and_get_token()
        response = self.client.post("/admin/save/nonsense/matheran-1",
                                    json={"blocks": []},
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 404)

    def test_upload_is_refused_without_a_session(self):
        response = self.client.post("/admin/upload")
        self.assertIn(response.status_code, (400, 302))

    def test_upload_rejects_an_empty_request(self):
        token = self.login_and_get_token()
        response = self.client.post("/admin/upload", data={},
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])


class BlogRouteTests(unittest.TestCase):
    """The post page must render the article, never the error page."""

    def setUp(self):
        self.client = blog.app.test_client()
        use_fake_db(self)

    def test_a_post_renders_instead_of_falling_into_the_error_page(self):
        response = self.client.get("/blog/sample-post")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="post-title"', body)
        self.assertIn("Sample Post", body)
        self.assertNotIn("Page Not Found", body)

    def test_an_unknown_post_still_gives_a_404(self):
        response = self.client.get("/blog/no-such-post")
        self.assertEqual(response.status_code, 404)

    def test_a_post_in_the_work_category_also_renders(self):
        response = self.client.get("/blog/sample-post")
        self.assertIn("Work", response.get_data(as_text=True))


class SeoTests(unittest.TestCase):
    """Slugs, meta text, crawler files and the tags in the <head>."""

    def setUp(self):
        self.client = blog.app.test_client()
        use_fake_db(self)

    def test_slugify(self):
        self.assertEqual(blog.slugify("Escaping the City: A Blissful Retreat!"),
                         "escaping-the-city-a-blissful-retreat")
        self.assertEqual(blog.slugify("  --Hello--World--  "), "hello-world")
        self.assertEqual(blog.slugify(""), "")
        self.assertEqual(blog.slugify(None), "")

    def test_slugify_caps_the_length(self):
        self.assertLessEqual(len(blog.slugify("a" * 500)), 80)

    def test_plain_text_strips_html(self):
        self.assertEqual(blog.plain_text("<p>Hello <b>there</b></p>"),
                         "Hello there")
        self.assertEqual(blog.plain_text(""), "")
        self.assertEqual(blog.plain_text(None), "")

    def test_plain_text_truncates_on_a_word_boundary(self):
        result = blog.plain_text("word " * 60)
        self.assertTrue(result.endswith("…"))
        self.assertLessEqual(len(result), 156)

    def test_absolute_prefers_the_configured_site_url(self):
        with mock.patch.object(blog, "SITE_URL", "https://example.com"):
            self.assertEqual(blog.absolute("/blog/x"),
                             "https://example.com/blog/x")
            self.assertEqual(blog.absolute("https://other.com/y"),
                             "https://other.com/y")
            self.assertEqual(blog.absolute(""), "")

    def test_robots_points_at_the_sitemap_and_blocks_admin(self):
        response = self.client.get("/robots.txt")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Sitemap:", body)
        self.assertIn("Disallow: /admin", body)
        self.assertIn("Disallow: /login", body)

    def test_sitemap_lists_the_pages_and_every_post(self):
        response = self.client.get("/sitemap.xml")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("<urlset", body)
        self.assertIn("/about_us", body)
        self.assertIn("/blog/sample-post", body)

    def test_post_page_has_canonical_open_graph_and_structured_data(self):
        body = self.client.get("/blog/sample-post").get_data(as_text=True)

        self.assertIn('rel="canonical"', body)
        self.assertIn('property="og:title"', body)
        self.assertIn('property="og:image"', body)
        self.assertIn('name="twitter:card"', body)
        self.assertIn('"@type": "BlogPosting"', body)

    def test_listing_pages_carry_website_structured_data(self):
        body = self.client.get("/").get_data(as_text=True)

        self.assertIn('rel="canonical"', body)
        self.assertIn('"@type": "WebSite"', body)

    def test_no_page_links_to_a_missing_manifest(self):
        for path in ("/", "/about_us", "/travel", "/work", "/blog/sample-post"):
            self.assertNotIn("site.webmanifest",
                             self.client.get(path).get_data(as_text=True))


class CreatePostTests(unittest.TestCase):
    """Creating a post must land you in the editor, not a form."""

    def setUp(self):
        self.client = blog.app.test_client()
        use_fake_db(self)
        use_admin_credentials(self)

    def test_anonymous_visitors_cannot_create_posts(self):
        self.assertEqual(self.client.get("/admin/new/lifestyle").status_code, 302)

    def test_new_post_redirects_into_the_inline_editor(self):
        self.client.post("/login",
                         data={"username": "admin", "password": "secret"})
        response = self.client.get("/admin/new/lifestyle")

        self.assertEqual(response.status_code, 302)
        self.assertIn("edit=1", response.headers["Location"])

    def test_an_unknown_category_is_rejected(self):
        self.client.post("/login",
                         data={"username": "admin", "password": "secret"})
        self.assertEqual(self.client.get("/admin/new/nonsense").status_code, 404)


if __name__ == "__main__":
    unittest.main()


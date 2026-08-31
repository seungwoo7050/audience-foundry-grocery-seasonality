import hashlib
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

from config.settings import staticfiles_storage_backend


class StaticDeliverySettingsTests(SimpleTestCase):
    def test_whitenoise_is_inside_security_and_before_application_middleware(self) -> None:
        middleware = list(settings.MIDDLEWARE)

        security = middleware.index("django.middleware.security.SecurityMiddleware")
        whitenoise = middleware.index("whitenoise.middleware.WhiteNoiseMiddleware")
        application = middleware.index("grocery.security.SecurityHeadersMiddleware")

        self.assertEqual(whitenoise, security + 1)
        self.assertLess(whitenoise, application)

    def test_static_build_uses_compressed_manifest_storage(self) -> None:
        self.assertEqual(
            staticfiles_storage_backend(debug=False),
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )
        self.assertEqual(
            staticfiles_storage_backend(debug=True),
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )

    def test_frontend_static_assets_are_local_and_discoverable(self) -> None:
        for asset in (
            "grocery/app.css",
            "grocery/brand-mark.svg",
            "grocery/favicon.svg",
            "grocery/fonts/hahmlet-bold.woff2",
        ):
            with self.subTest(asset=asset):
                self.assertIsNotNone(finders.find(asset))

        css = Path(settings.BASE_DIR, "grocery", "static", "grocery", "app.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('url("fonts/hahmlet-bold.woff2")', css)
        self.assertNotIn("@import", css)
        self.assertNotIn("http://", css)
        self.assertNotIn("https://", css)

    def test_self_hosted_heading_font_matches_pinned_upstream_provenance(self) -> None:
        base_dir = Path(settings.BASE_DIR)
        font_path = base_dir / "grocery/static/grocery/fonts/hahmlet-bold.woff2"
        license_path = base_dir / "LICENSES/Hahmlet-OFL-1.1.txt"
        notices_path = base_dir / "THIRD_PARTY_NOTICES.md"

        self.assertEqual(
            hashlib.sha256(font_path.read_bytes()).hexdigest(),
            "9a5ab61f43a689167d0dea3046003bc3a897f32ab3af7c437add32075c15c948",
        )
        license_text = license_path.read_text(encoding="utf-8")
        self.assertIn(
            "Copyright 2020 The Hahmlet Project Authors",
            license_text,
        )
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", license_text)
        notices = notices_path.read_text(encoding="utf-8")
        self.assertIn("f9c5dac25d88015e9f0953253cec1a71854b7d24", notices)
        self.assertIn("LICENSES/Hahmlet-OFL-1.1.txt", notices)

    def test_public_frontend_contains_no_raster_photo_assets(self) -> None:
        static_root = Path(settings.BASE_DIR, "grocery", "static", "grocery")
        raster_suffixes = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}

        raster_assets = [
            path.relative_to(static_root).as_posix()
            for path in static_root.rglob("*")
            if path.is_file() and path.suffix.lower() in raster_suffixes
        ]

        self.assertEqual(raster_assets, [])

    def test_svg_assets_have_no_script_foreign_object_or_external_reference(self) -> None:
        for asset_name in ("brand-mark.svg", "favicon.svg"):
            path = Path(settings.BASE_DIR, "grocery", "static", "grocery", asset_name)
            # This is a repository-owned static fixture, not untrusted XML input.
            root = ElementTree.parse(path).getroot()  # noqa: S314
            local_names = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}

            with self.subTest(asset=asset_name):
                self.assertEqual(root.tag.rsplit("}", 1)[-1], "svg")
                self.assertIn("viewBox", root.attrib)
                self.assertNotIn("script", local_names)
                self.assertNotIn("foreignObject", local_names)
                for element in root.iter():
                    for name, value in element.attrib.items():
                        self.assertFalse(name.lower().startswith("on"))
                        if name.rsplit("}", 1)[-1] == "href":
                            self.assertTrue(value.startswith("#"), value)

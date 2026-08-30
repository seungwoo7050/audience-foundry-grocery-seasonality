from django.conf import settings
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

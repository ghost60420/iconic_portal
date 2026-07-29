import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


class ProductionSecuritySettingsTests(SimpleTestCase):
    project_root = Path(__file__).resolve().parents[2]
    security_env_names = {
        "DJANGO_SECURE_SSL_REDIRECT",
        "DJANGO_SESSION_COOKIE_SECURE",
        "DJANGO_CSRF_COOKIE_SECURE",
        "DJANGO_SECURE_HSTS_SECONDS",
        "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
        "DJANGO_SECURE_HSTS_PRELOAD",
        "DJANGO_TRUST_X_FORWARDED_PROTO",
        "DJANGO_CSRF_TRUSTED_ORIGINS",
    }

    def settings_environment(self, **overrides):
        environment = os.environ.copy()
        for name in self.security_env_names:
            environment.pop(name, None)
        environment.update(
            {
                "DJANGO_DEBUG": "0",
                "DJANGO_SECRET_KEY": (
                    "stage11-test-only-uQ9p2Yx7Kc4Vn8Lm3Rs6Wd1Bf5Hj0ZaE"
                    "tG7iN2oP9sX4"
                ),
                **overrides,
            }
        )
        return environment

    def read_security_settings(self, **overrides):
        script = """
import json
from iconic_site import settings
print(json.dumps({
    "ssl_redirect": settings.SECURE_SSL_REDIRECT,
    "session_secure": settings.SESSION_COOKIE_SECURE,
    "csrf_secure": settings.CSRF_COOKIE_SECURE,
    "hsts_seconds": settings.SECURE_HSTS_SECONDS,
    "hsts_subdomains": settings.SECURE_HSTS_INCLUDE_SUBDOMAINS,
    "hsts_preload": settings.SECURE_HSTS_PRELOAD,
    "proxy_header": getattr(settings, "SECURE_PROXY_SSL_HEADER", None),
    "csrf_origins": settings.CSRF_TRUSTED_ORIGINS,
}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.project_root,
            env=self.settings_environment(**overrides),
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout.strip())

    def test_secure_transport_controls_are_explicit(self):
        configured = self.read_security_settings(
            DJANGO_SECURE_SSL_REDIRECT="1",
            DJANGO_SESSION_COOKIE_SECURE="true",
            DJANGO_CSRF_COOKIE_SECURE="yes",
            DJANGO_SECURE_HSTS_SECONDS="31536000",
            DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS="on",
            DJANGO_SECURE_HSTS_PRELOAD="1",
            DJANGO_TRUST_X_FORWARDED_PROTO="1",
            DJANGO_CSRF_TRUSTED_ORIGINS="https://crm.example.com",
        )

        self.assertTrue(configured["ssl_redirect"])
        self.assertTrue(configured["session_secure"])
        self.assertTrue(configured["csrf_secure"])
        self.assertEqual(configured["hsts_seconds"], 31536000)
        self.assertTrue(configured["hsts_subdomains"])
        self.assertTrue(configured["hsts_preload"])
        self.assertEqual(
            configured["proxy_header"],
            ["HTTP_X_FORWARDED_PROTO", "https"],
        )
        self.assertIn("https://crm.example.com", configured["csrf_origins"])

    def test_local_transport_behavior_is_unchanged_without_environment_flags(self):
        configured = self.read_security_settings()

        self.assertFalse(configured["ssl_redirect"])
        self.assertFalse(configured["session_secure"])
        self.assertFalse(configured["csrf_secure"])
        self.assertEqual(configured["hsts_seconds"], 0)
        self.assertFalse(configured["hsts_subdomains"])
        self.assertFalse(configured["hsts_preload"])
        self.assertIsNone(configured["proxy_header"])

    def test_deploy_check_passes_with_complete_production_configuration(self):
        result = subprocess.run(
            [sys.executable, "manage.py", "check", "--deploy"],
            cwd=self.project_root,
            env=self.settings_environment(
                DJANGO_SECURE_SSL_REDIRECT="1",
                DJANGO_SESSION_COOKIE_SECURE="1",
                DJANGO_CSRF_COOKIE_SECURE="1",
                DJANGO_SECURE_HSTS_SECONDS="31536000",
                DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS="1",
                DJANGO_SECURE_HSTS_PRELOAD="1",
                DJANGO_TRUST_X_FORWARDED_PROTO="1",
            ),
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn(
            "System check identified no issues (0 silenced).",
            result.stdout + result.stderr,
        )

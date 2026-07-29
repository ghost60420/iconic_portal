# KPI Production Security Check

## Result

Production security gate: `FAIL`.

The unchanged live application reports five `manage.py check --deploy`
warnings:

1. `security.W004`: HSTS is not configured.
2. `security.W008`: Django SSL redirect is disabled.
3. `security.W009`: the production secret does not meet Django strength
   guidance.
4. `security.W012`: the session cookie is not Secure.
5. `security.W016`: the CSRF cookie is not Secure.

## Verified Live Controls

| Control | Result |
| --- | --- |
| `DEBUG` | Disabled |
| HTTP to HTTPS | Nginx returns `301` |
| TLS certificate | Valid for `femline.ca`, expires 2026-10-03 |
| TLS protocols | TLS 1.2 and 1.3 |
| HSTS response header | Missing |
| CSRF cookie Secure flag | Missing |
| Session cookie Secure flag | Not configured in Django |
| Allowed hosts | Five explicit hosts |
| CSRF middleware | Enabled |
| CSRF trusted origins | Explicit HTTPS origins |
| Proxy scheme header | Nginx sets `X-Forwarded-Proto` |
| Secret source | Owner-only `.env`, mode `600` |
| Secret strength | FAIL according to Django |
| Static directory | Mode `755`, Nginx alias |
| Media directory | Mode `755`, Nginx alias |
| Frame protection | `DENY` |
| Content type protection | `nosniff` |
| Referrer policy | `same-origin` |

Media files are served directly by Nginx under `/media/`. The current
configuration does not prove private-file authorization at the web-server
layer; any sensitive KPI evidence upload must use the application-protected
file path before release.

## Release-Gate Code

`iconic_site/settings.py` now supports explicit environment values for:

- `DJANGO_SECURE_SSL_REDIRECT`
- `DJANGO_SESSION_COOKIE_SECURE`
- `DJANGO_CSRF_COOKIE_SECURE`
- `DJANGO_SECURE_HSTS_SECONDS`
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `DJANGO_SECURE_HSTS_PRELOAD`
- `DJANGO_TRUST_X_FORWARDED_PROTO`
- `DJANGO_CSRF_TRUSTED_ORIGINS`

Local defaults preserve existing development and test behavior. Three focused
tests pass. A simulated complete production environment returns:

`System check identified no issues (0 silenced).`

This code is not deployed and no production environment value was changed.

## Required Closure

1. Generate and install a strong random production secret through the approved
   secret store, with a session-impact plan.
2. Set secure redirect/cookie/proxy values in production.
3. Stage HSTS deliberately, then approve subdomain and preload behavior.
4. Run `manage.py check --deploy` in the final production configuration and
   require zero serious warnings.
5. Verify login, CSRF submission, proxy redirects, and Secure cookie flags.
6. Confirm whether KPI evidence media requires authenticated delivery.

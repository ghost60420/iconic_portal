import html
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

try:
    import certifi
except Exception:  # pragma: no cover - optional dependency fallback
    certifi = None


USER_AGENT = "Mozilla/5.0 (compatible; LeadBrainShopifyDirectory/1.0; +https://femline.ca)"
REQUEST_TIMEOUT = 3
MAX_RESPONSE_BYTES = 180000
SEARCH_TIMEOUT = 4

SHOPIFY_DIRECTORY_SOURCE = "shopify_clothing_directory"
SHOPIFY_DIRECTORY_SOURCE_DETAIL = "Shopify Clothing Directory"

SHOPIFY_DIRECTORY_TERMS = [
    "clothing",
    "apparel",
    "fashion",
    "streetwear",
    "activewear",
    "kidswear",
    "swimwear",
    "hoodies",
    "hoodie",
    "t shirts",
    "t shirt",
    "tees",
    "merch",
    "collection",
    "product",
]

SHOPIFY_HTML_SIGNALS = [
    "cdn.shopify.com",
    "shopify-payment-button",
    "shopify-section",
    "myshopify.com",
    "Shopify.theme",
    "shopify-features",
    "shopify-buy__",
]

NORTH_AMERICA_TERMS = [
    "canada",
    "usa",
    "united states",
    "north america",
    "shipping to canada",
    "shipping to the usa",
    "cad",
    "usd",
]

SEARCH_PLATFORM_QUERIES = [
    "site:myshopify.com clothing brand {country}",
    "Shopify {niche} brand {country}",
    "{niche} brand Shopify {country}",
    "{niche} Shopify {country} clothing",
]


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_url(url):
    value = _text(url)
    if not value:
        return ""
    if "://" not in value and "." in value and " " not in value:
        value = f"https://{value}"
    return value[:200]


def root_url(url):
    normalized = normalize_url(url)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    scheme = parsed.scheme or "https"
    if not parsed.netloc:
        return ""
    return f"{scheme}://{parsed.netloc}".rstrip("/")


def website_key(url):
    normalized = normalize_url(url).lower()
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    host = parsed.netloc or parsed.path
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or ""
    if path and path != "/":
        return f"{host}{path.rstrip('/')}"
    return host.rstrip("/")


def domain_key(url):
    normalized = normalize_url(url).lower()
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    host = parsed.netloc or parsed.path
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip("/")


def source_detail_label(source_type: str) -> str:
    if source_type == SHOPIFY_DIRECTORY_SOURCE:
        return SHOPIFY_DIRECTORY_SOURCE_DETAIL
    return _text(source_type).replace("_", " ").title()


def query_countries(selected_country: str) -> list[str]:
    country = _text(selected_country)
    if country == "North America":
        return ["Canada", "USA"]
    return [country] if country else []


def search_query_results(query, *, limit=10):
    search_url = f"https://html.duckduckgo.com/html/?q={quote(_text(query))}"
    response, error = _safe_http_get(search_url, timeout=SEARCH_TIMEOUT)
    if not response:
        return {"results": [], "error": error}
    return {
        "results": _search_results_from_html(response["text"])[: max(1, limit)],
        "error": "",
    }


def build_shopify_directory_queries(country: str, niche_label: str) -> list[str]:
    queries = []
    seen = set()
    niche = _text(niche_label).replace("_", " ").strip()
    for template in SEARCH_PLATFORM_QUERIES:
        query = template.format(country=_text(country), niche=niche)
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        queries.append(query)
    if niche and niche not in {"fashion", "clothing", "apparel"}:
        extra_query = f"Shopify {niche} store {country}"
        queries.append(extra_query)
    return queries


def normalize_candidate_website(url: str) -> str:
    root = root_url(url)
    if root:
        return root
    return normalize_url(url)


def candidate_match_key(url: str) -> str:
    root = root_url(url)
    if root:
        return domain_key(root)
    return website_key(url)


def detect_apparel_terms(text: str) -> list[str]:
    lowered = _text(text).lower()
    hits = []
    for term in SHOPIFY_DIRECTORY_TERMS:
        if term in lowered:
            hits.append(term)
    return sorted(set(hits))


def enrich_shopify_research(*, website: str, homepage_html: str = "", homepage_status: str = "", requested_country: str = "") -> dict:
    root = root_url(website) or normalize_candidate_website(website)
    result = {
        "website_status": homepage_status or "missing",
        "official_website_found": root,
        "public_email_found": "",
        "public_phone_found": "",
        "business_description": "",
        "search_summary": "",
        "apparel_signals": [],
        "confidence_notes": "",
        "shopify_signal_found": False,
        "shopify_signal_reasons": [],
        "shopify_product_pages_found": 0,
        "shopify_collection_pages_found": 0,
        "product_or_collection_found": False,
        "contact_page_found": False,
        "north_america_signal_found": False,
        "robots_txt_checked": False,
        "robots_txt_url": "",
        "robots_disallowed_examples": [],
        "sitemap_checked": False,
        "checked_urls": [],
    }
    if not root:
        result["confidence_notes"] = "No candidate website was available."
        return result

    robots = _load_robots(root)
    result["robots_txt_checked"] = True
    result["robots_txt_url"] = robots["robots_url"]

    combined_texts = []
    primary_html = homepage_html
    primary_status = homepage_status or "missing"

    if not primary_html and _can_fetch(robots["parser"], root):
        response, error = _safe_http_get(root, timeout=REQUEST_TIMEOUT)
        if response:
            primary_html = response["text"]
            result["official_website_found"] = response["url"]
            primary_status = "redirect" if response["url"].rstrip("/") != root.rstrip("/") else "live"
        else:
            primary_status = "failed"
            if error:
                result["confidence_notes"] = f"Homepage request failed: {error}"
    elif not primary_html and not _can_fetch(robots["parser"], root):
        primary_status = "failed"
        result["robots_disallowed_examples"].append(root)
        result["confidence_notes"] = "robots.txt blocked the homepage from a safe read."

    result["website_status"] = primary_status or result["website_status"]
    if primary_html:
        combined_texts.append(primary_html[:8000])
        result["checked_urls"].append(result["official_website_found"] or root)
        title = _extract_title(primary_html)
        description = _extract_meta_description(primary_html)
        result["business_description"] = description or title
        result["public_email_found"] = _extract_email(primary_html)
        result["public_phone_found"] = _extract_phone(primary_html)
        signal_reasons = _detect_shopify_signals(primary_html, result["official_website_found"] or root)
        if signal_reasons:
            result["shopify_signal_found"] = True
            result["shopify_signal_reasons"] = signal_reasons

    candidate_urls = _extract_candidate_urls(
        root=root,
        homepage_html=primary_html,
        robots_parser=robots["parser"],
    )
    result["sitemap_checked"] = True
    for page_type, url in candidate_urls:
        response, _ = _safe_http_get(url, timeout=REQUEST_TIMEOUT)
        if not response:
            continue
        page_text = response["text"][:8000]
        combined_texts.append(page_text)
        result["checked_urls"].append(response["url"])
        if not result["public_email_found"]:
            result["public_email_found"] = _extract_email(page_text)
        if not result["public_phone_found"]:
            result["public_phone_found"] = _extract_phone(page_text)
        if page_type == "product":
            result["shopify_product_pages_found"] += 1
        elif page_type == "collection":
            result["shopify_collection_pages_found"] += 1
        elif page_type == "contact":
            result["contact_page_found"] = True
        signal_reasons = _detect_shopify_signals(page_text, response["url"])
        if signal_reasons:
            result["shopify_signal_found"] = True
            result["shopify_signal_reasons"] = sorted(set(result["shopify_signal_reasons"]) | set(signal_reasons))

    combined_text = " ".join(part for part in combined_texts if part)
    result["apparel_signals"] = detect_apparel_terms(combined_text)
    result["product_or_collection_found"] = bool(
        result["shopify_product_pages_found"] or result["shopify_collection_pages_found"]
    )
    result["north_america_signal_found"] = _has_north_america_signal(
        combined_text,
        requested_country=requested_country,
        website=result["official_website_found"] or root,
    )

    summary_parts = []
    if result["shopify_signal_found"]:
        summary_parts.append("Shopify public signals found")
    if result["product_or_collection_found"]:
        summary_parts.append("product or collection pages found")
    if result["contact_page_found"]:
        summary_parts.append("contact page found")
    if result["north_america_signal_found"]:
        summary_parts.append("North America signal found")
    if not summary_parts:
        summary_parts.append("Shopify-specific signals were limited")
    result["search_summary"] = ". ".join(summary_parts)[:500]
    result["confidence_notes"] = ". ".join(summary_parts)[:500]
    return result


def _http_get(url, timeout=REQUEST_TIMEOUT):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    context = _ssl_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        raw = response.read(MAX_RESPONSE_BYTES)
        text = raw.decode("utf-8", errors="ignore")
        return {
            "url": response.geturl(),
            "status_code": getattr(response, "status", 200),
            "text": text,
        }


def _safe_http_get(url, timeout=REQUEST_TIMEOUT):
    try:
        return _http_get(url, timeout=timeout), ""
    except HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except URLError as exc:
        return None, _text(exc.reason) or "network error"
    except Exception as exc:
        return None, _text(exc) or "request failed"


def _ssl_context():
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _load_robots(root: str) -> dict:
    robots_url = urljoin(root.rstrip("/") + "/", "robots.txt")
    parser = RobotFileParser()
    parser.set_url(robots_url)
    response, _ = _safe_http_get(robots_url, timeout=REQUEST_TIMEOUT)
    if not response:
        return {"parser": None, "robots_url": robots_url}
    parser.parse((response["text"] or "").splitlines())
    return {"parser": parser, "robots_url": robots_url}


def _can_fetch(parser: RobotFileParser | None, url: str) -> bool:
    if parser is None:
        return True
    try:
        return parser.can_fetch("LeadBrainShopifyDirectory", url)
    except Exception:
        return True


def _extract_title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.I | re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()


def _extract_meta_description(text: str) -> str:
    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I | re.S)
        if match:
            return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
    return ""


def _extract_email(text: str) -> str:
    match = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", text or "", re.I)
    return _text(match.group(0)).lower() if match else ""


def _extract_phone(text: str) -> str:
    match = re.search(r"(\+?\d[\d\s().\-]{7,}\d)", text or "")
    return _text(match.group(1)) if match else ""


def _detect_shopify_signals(text: str, url: str) -> list[str]:
    lowered = _text(text).lower()
    hits = []
    for hint in SHOPIFY_HTML_SIGNALS:
        if hint.lower() in lowered:
            hits.append(hint)
    if "myshopify.com" in _text(url).lower():
        hits.append("myshopify.com")
    if "/products/" in lowered:
        hits.append("/products/")
    if "/collections/" in lowered:
        hits.append("/collections/")
    return sorted(set(hits))


def _has_north_america_signal(text: str, *, requested_country: str = "", website: str = "") -> bool:
    lowered = _text(text).lower()
    if any(term in lowered for term in NORTH_AMERICA_TERMS):
        return True
    host = domain_key(website)
    if requested_country == "Canada" and host.endswith(".ca"):
        return True
    return False


def _extract_links(text: str, base_url: str) -> list[str]:
    links = []
    for href in re.findall(r"""href=["']([^"']+)["']""", text or "", re.I):
        absolute = normalize_url(urljoin(base_url, html.unescape(href)))
        if absolute.startswith("http"):
            links.append(absolute)
    return links


def _interesting_shopify_urls(root: str, homepage_html: str) -> list[tuple[str, str]]:
    root_host = domain_key(root)
    found: list[tuple[str, str]] = []
    seen = set()
    for url in _extract_links(homepage_html, root):
        if domain_key(url) != root_host:
            continue
        lowered = url.lower()
        page_type = ""
        if "/products/" in lowered:
            page_type = "product"
        elif "/collections/" in lowered:
            page_type = "collection"
        elif "/contact" in lowered:
            page_type = "contact"
        if not page_type:
            continue
        key = f"{page_type}:{url}"
        if key in seen:
            continue
        seen.add(key)
        found.append((page_type, url))
    return found


def _sitemap_candidate_urls(root: str, robots_parser: RobotFileParser | None) -> list[tuple[str, str]]:
    sitemap_url = urljoin(root.rstrip("/") + "/", "sitemap.xml")
    if not _can_fetch(robots_parser, sitemap_url):
        return []
    response, _ = _safe_http_get(sitemap_url, timeout=REQUEST_TIMEOUT)
    if not response:
        return []

    urls = []
    locs = re.findall(r"<loc>(.*?)</loc>", response["text"] or "", re.I)
    nested = []
    for loc in locs[:30]:
        url = normalize_url(html.unescape(loc))
        lowered = url.lower()
        if "sitemap_products" in lowered or "sitemap_collections" in lowered or "sitemap_pages" in lowered:
            nested.append(url)
            continue
        page_type = ""
        if "/products/" in lowered:
            page_type = "product"
        elif "/collections/" in lowered:
            page_type = "collection"
        elif "/contact" in lowered:
            page_type = "contact"
        if page_type and _can_fetch(robots_parser, url):
            urls.append((page_type, url))
        if len(urls) >= 3:
            return urls[:3]

    for nested_url in nested[:2]:
        if not _can_fetch(robots_parser, nested_url):
            continue
        nested_response, _ = _safe_http_get(nested_url, timeout=REQUEST_TIMEOUT)
        if not nested_response:
            continue
        for loc in re.findall(r"<loc>(.*?)</loc>", nested_response["text"] or "", re.I):
            url = normalize_url(html.unescape(loc))
            lowered = url.lower()
            page_type = ""
            if "/products/" in lowered:
                page_type = "product"
            elif "/collections/" in lowered:
                page_type = "collection"
            elif "/contact" in lowered:
                page_type = "contact"
            if page_type and _can_fetch(robots_parser, url):
                urls.append((page_type, url))
            if len(urls) >= 3:
                return urls[:3]
    return urls[:3]


def _extract_candidate_urls(*, root: str, homepage_html: str, robots_parser: RobotFileParser | None) -> list[tuple[str, str]]:
    picked: list[tuple[str, str]] = []
    seen_types = set()
    for page_type, url in _interesting_shopify_urls(root, homepage_html):
        if page_type in seen_types or not _can_fetch(robots_parser, url):
            continue
        picked.append((page_type, url))
        seen_types.add(page_type)
        if len(picked) >= 3:
            return picked
    for page_type, url in _sitemap_candidate_urls(root, robots_parser):
        if page_type in seen_types:
            continue
        picked.append((page_type, url))
        seen_types.add(page_type)
        if len(picked) >= 3:
            break
    return picked[:3]


def _search_results_from_html(text):
    results = []
    pattern = re.compile(
        r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>(?P<tail>.*?)(?=<a[^>]+href=|$)',
        re.I | re.S,
    )
    for match in pattern.finditer(text or ""):
        href = _decode_search_url(html.unescape(match.group("href")))
        title = re.sub(r"<.*?>", " ", html.unescape(match.group("title")))
        snippet = re.sub(r"<.*?>", " ", html.unescape(match.group("tail")))
        title = re.sub(r"\s+", " ", title).strip()
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if not title or not href.startswith("http"):
            continue
        results.append({"title": title, "url": href, "snippet": snippet[:320]})
        if len(results) >= 10:
            break
    return results


def _decode_search_url(url):
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        encoded_match = re.search(r"[?&]uddg=([^&]+)", parsed.query)
        if encoded_match:
            return normalize_url(unquote(encoded_match.group(1)))
    return normalize_url(url)

import html
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen


USER_AGENT = "Mozilla/5.0 (compatible; LeadBrainLite/1.0; +https://femline.ca)"
APPAREL_TERMS = [
    "apparel",
    "clothing",
    "fashion",
    "streetwear",
    "activewear",
    "sportswear",
    "kidswear",
    "merch",
    "uniform",
    "private label",
    "clothing brand",
]
SOCIAL_HOST_HINTS = {
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "tiktok.com",
}


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize_url(url):
    value = _text(url)
    if not value:
        return ""
    if "://" not in value and "." in value and " " not in value:
        value = f"https://{value}"
    return value[:200]


def _http_get(url, timeout=8):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(200000)
        content_type = response.headers.get("Content-Type", "")
        text = raw.decode("utf-8", errors="ignore")
        return {
            "url": response.geturl(),
            "status_code": getattr(response, "status", 200),
            "content_type": content_type,
            "text": text,
        }


def _safe_http_get(url, timeout=8):
    try:
        return _http_get(url, timeout=timeout), ""
    except HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except URLError as exc:
        return None, _text(exc.reason) or "network error"
    except Exception as exc:
        return None, _text(exc) or "request failed"


def _extract_title(text):
    match = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.I | re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()


def _extract_meta_description(text):
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


def _extract_linkedin_url(text):
    match = re.search(r"https?://(?:www\.)?linkedin\.com/company/[A-Za-z0-9\-_%/]+", text or "", re.I)
    return _text(match.group(0)) if match else ""


def _extract_email(text):
    match = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", text or "", re.I)
    return _text(match.group(0)).lower() if match else ""


def _extract_phone(text):
    match = re.search(r"(\+?\d[\d\s().\-]{7,}\d)", text or "")
    return _text(match.group(1)) if match else ""


def _extract_contact_title(text):
    lowered = _text(text).lower()
    titles = [
        "founder",
        "co founder",
        "owner",
        "buyer",
        "head of sourcing",
        "sourcing manager",
        "creative director",
        "merchandising manager",
        "operations manager",
        "ceo",
    ]
    for title in titles:
        if title in lowered:
            return title.title()
    return ""


def _extract_contact_name(text):
    patterns = [
        r"(?:Founder|Owner|CEO|Buyer|Director)\s*[:\-]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*(?:Founder|Owner|CEO|Buyer|Director)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            return _text(match.group(1))
    return ""


def detect_apparel_signals(text):
    haystack = _text(text).lower()
    signals = []
    for term in APPAREL_TERMS:
        if term in haystack:
            signals.append(term)
    return sorted(set(signals))


def check_website_status(url):
    normalized = _normalize_url(url)
    if not normalized:
        return {
            "status": "missing",
            "final_url": "",
            "status_code": 0,
            "error": "",
        }

    response, error = _safe_http_get(normalized, timeout=6)
    if not response:
        return {
            "status": "failed",
            "final_url": normalized,
            "status_code": 0,
            "error": error,
        }

    final_url = response["url"]
    status = "redirect" if final_url.rstrip("/") != normalized.rstrip("/") else "live"
    return {
        "status": status,
        "final_url": final_url,
        "status_code": response["status_code"],
        "error": "",
    }


def _decode_search_url(url):
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        encoded = parse_qs(parsed.query).get("uddg", [""])[0]
        if encoded:
            return _normalize_url(unquote(encoded))
    return _normalize_url(url)


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
        results.append(
            {
                "title": title,
                "url": href,
                "snippet": snippet[:320],
            }
        )
        if len(results) >= 6:
            break
    return results


def search_business_online(company_name, website=""):
    query_parts = [_text(company_name)]
    if _text(website):
        query_parts.append(_text(website))
    query_parts.append("apparel")
    query = " ".join(part for part in query_parts if part)
    if not query.strip():
        return {
            "official_website_found": "",
            "linkedin_url_found": "",
            "public_email_found": "",
            "public_phone_found": "",
            "business_description": "",
            "apparel_signals": [],
            "search_summary": "",
            "possible_contact_name": "",
            "possible_contact_title": "",
            "confidence_notes": "No search query was available.",
            "search_results": [],
        }

    search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    response, error = _safe_http_get(search_url, timeout=8)
    if not response:
        return {
            "official_website_found": "",
            "linkedin_url_found": "",
            "public_email_found": "",
            "public_phone_found": "",
            "business_description": "",
            "apparel_signals": [],
            "search_summary": "",
            "possible_contact_name": "",
            "possible_contact_title": "",
            "confidence_notes": f"Public search lookup failed: {error}",
            "search_results": [],
        }

    results = _search_results_from_html(response["text"])
    official_website = ""
    linkedin_url = ""
    snippets = []
    emails = []
    phones = []

    for result in results:
        parsed = urlparse(result["url"])
        netloc = parsed.netloc.lower()
        snippets.append(" ".join(part for part in [result["title"], result["snippet"]] if part))
        email = _extract_email(result["snippet"])
        phone = _extract_phone(result["snippet"])
        if email:
            emails.append(email)
        if phone:
            phones.append(phone)
        if not linkedin_url and "linkedin.com/company" in result["url"]:
            linkedin_url = result["url"]
        if not official_website and netloc and not any(hint in netloc for hint in SOCIAL_HOST_HINTS):
            official_website = result["url"]

    search_summary = " ".join(snippets[:2]).strip()
    description = snippets[0] if snippets else ""
    combined_text = " ".join(snippets)

    return {
        "official_website_found": official_website,
        "linkedin_url_found": linkedin_url,
        "public_email_found": emails[0] if emails else "",
        "public_phone_found": phones[0] if phones else "",
        "business_description": description[:500],
        "apparel_signals": detect_apparel_signals(combined_text),
        "search_summary": search_summary[:600],
        "possible_contact_name": _extract_contact_name(combined_text),
        "possible_contact_title": _extract_contact_title(combined_text),
        "confidence_notes": "Public search results were used to confirm the business.",
        "search_results": results,
    }


def _infer_business_type(text):
    lowered = _text(text).lower()
    if not lowered:
        return ""
    if "private label" in lowered or "manufacturer" in lowered or "factory" in lowered:
        return "Manufacturer / Private Label"
    if "uniform" in lowered:
        return "Uniform Supplier"
    if "merch" in lowered:
        return "Merch Brand"
    if any(term in lowered for term in ["apparel", "clothing", "fashion", "streetwear", "sportswear", "activewear"]):
        return "Apparel Brand"
    return ""


def research_company(company):
    website = _normalize_url(getattr(company, "website", ""))
    research = {
        "website_status": "missing",
        "official_website_found": "",
        "linkedin_url_found": "",
        "public_email_found": "",
        "public_phone_found": "",
        "business_description": "",
        "apparel_signals": [],
        "search_summary": "",
        "possible_contact_name": "",
        "possible_contact_title": "",
        "confidence_notes": "",
        "business_type_detected": "",
        "search_results": [],
    }

    website_status = check_website_status(website)
    research["website_status"] = website_status["status"]
    research["official_website_found"] = website_status["final_url"] if website_status["status"] in {"live", "redirect"} else ""

    page_text = ""
    if website_status["status"] in {"live", "redirect"} and website_status["final_url"]:
        response, error = _safe_http_get(website_status["final_url"], timeout=8)
        if response:
            title = _extract_title(response["text"])
            description = _extract_meta_description(response["text"])
            page_text = " ".join([title, description, response["text"][:4000]])
            research["business_description"] = description or title
            research["linkedin_url_found"] = _extract_linkedin_url(response["text"])
            research["public_email_found"] = _extract_email(response["text"])
            research["public_phone_found"] = _extract_phone(response["text"])
            research["possible_contact_name"] = _extract_contact_name(response["text"])
            research["possible_contact_title"] = _extract_contact_title(response["text"])
            research["confidence_notes"] = "A live public website was found."
        else:
            research["confidence_notes"] = f"Website lookup had partial failure: {error}"

    if not research["official_website_found"] or not research["business_description"]:
        search_data = search_business_online(getattr(company, "company_name", ""), website=website)
        for key in [
            "official_website_found",
            "linkedin_url_found",
            "public_email_found",
            "public_phone_found",
            "business_description",
            "search_summary",
            "possible_contact_name",
            "possible_contact_title",
            "confidence_notes",
            "search_results",
        ]:
            if not research.get(key):
                research[key] = search_data.get(key, research.get(key))
        research["apparel_signals"] = sorted(set(research["apparel_signals"]) | set(search_data.get("apparel_signals", [])))
        if not research["official_website_found"]:
            research["official_website_found"] = search_data.get("official_website_found", "")

    combined_text = " ".join(
        part
        for part in [
            page_text,
            research.get("business_description", ""),
            research.get("search_summary", ""),
            getattr(company, "company_name", ""),
        ]
        if part
    )
    research["apparel_signals"] = sorted(set(research["apparel_signals"]) | set(detect_apparel_signals(combined_text)))
    research["business_type_detected"] = _infer_business_type(combined_text)

    if not research["confidence_notes"]:
        if research["official_website_found"]:
            research["confidence_notes"] = "The company appears to have a public web presence."
        elif research["search_summary"]:
            research["confidence_notes"] = "Only limited public search data was found."
        else:
            research["confidence_notes"] = "Public data could not be confirmed."

    return research

import re
import urllib.request
from urllib.parse import urljoin, urlparse
from html import unescape


USER_AGENT = "IconicCRMLeadResearch/1.0"


def _clean_text(value):
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_domain(url):
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    return parsed.netloc.lower()


def fetch_url(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="ignore")


def extract_links(html, base_url):
    links = re.findall(r'href=[\"\\\']([^\"\\\']+)[\"\\\']', html, flags=re.IGNORECASE)
    clean = []
    for link in links:
        if link.startswith("mailto:") or link.startswith("tel:"):
            clean.append(link)
            continue
        if link.startswith("#"):
            continue
        clean.append(urljoin(base_url, link))
    return list(dict.fromkeys(clean))


def extract_emails(text):
    emails = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}", text, flags=re.IGNORECASE)
    return list(dict.fromkeys([e.lower() for e in emails]))


def extract_phones(text):
    phones = re.findall(r"(?:\\+?\\d[\\d\\s().-]{7,}\\d)", text)
    cleaned = []
    for p in phones:
        p = re.sub(r"[\\s().-]+", "", p)
        if len(p) >= 8:
            cleaned.append(p)
    return list(dict.fromkeys(cleaned))


def extract_social_links(links):
    socials = {"instagram": "", "linkedin": "", "facebook": "", "tiktok": "", "youtube": ""}
    for link in links:
        lower = link.lower()
        if "instagram.com" in lower and not socials["instagram"]:
            socials["instagram"] = link
        if "linkedin.com" in lower and not socials["linkedin"]:
            socials["linkedin"] = link
        if "facebook.com" in lower and not socials["facebook"]:
            socials["facebook"] = link
        if "tiktok.com" in lower and not socials["tiktok"]:
            socials["tiktok"] = link
        if "youtube.com" in lower and not socials["youtube"]:
            socials["youtube"] = link
    return socials


def pick_contact_pages(links):
    contact = ""
    about = ""
    for link in links:
        lower = link.lower()
        if not contact and ("contact" in lower or "support" in lower):
            contact = link
        if not about and ("about" in lower or "story" in lower):
            about = link
    return contact, about


def extract_company_name(html, fallback_domain=""):
    title_match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        title = _clean_text(unescape(title_match.group(1)))
        if title:
            return title[:200]
    return fallback_domain.replace("www.", "")[:200]


def analyze_website(url):
    base_domain = normalize_domain(url)
    page = fetch_url(url)
    links = extract_links(page, url)
    contact_page, about_page = pick_contact_pages(links)
    socials = extract_social_links(links)

    emails = extract_emails(page)
    phones = extract_phones(page)

    extra_pages = []
    for link in [contact_page, about_page]:
        if link and link not in extra_pages:
            extra_pages.append(link)

    for link in extra_pages[:2]:
        try:
            sub_page = fetch_url(link)
            emails.extend(extract_emails(sub_page))
            phones.extend(extract_phones(sub_page))
        except Exception:
            continue

    emails = list(dict.fromkeys([e.lower() for e in emails]))
    phones = list(dict.fromkeys(phones))

    company_name = extract_company_name(page, base_domain)

    return {
        "domain": base_domain,
        "company_name": company_name,
        "contact_page": contact_page,
        "about_page": about_page,
        "socials": socials,
        "emails": emails,
        "phones": phones,
    }


def recommend_channel(signals):
    if signals.get("emails"):
        return "Email"
    if signals.get("contact_page"):
        return "Contact form"
    socials = signals.get("socials", {})
    if socials.get("instagram"):
        return "Instagram"
    if socials.get("linkedin"):
        return "LinkedIn"
    if signals.get("phones"):
        return "Phone"
    return "Website"


def classify_fit(score):
    if score >= 70:
        return "Strong Fit"
    if score >= 40:
        return "Moderate Fit"
    if score >= 20:
        return "Weak Fit"
    return "Bad Fit"


def qualification_status(score, has_contact):
    fit_label = classify_fit(score)
    if fit_label == "Bad Fit":
        return "Bad Fit"
    if not has_contact:
        return "Contact Missing"
    if fit_label == "Strong Fit":
        return "Outreach Ready"
    if fit_label == "Moderate Fit":
        return "Qualified"
    return "Needs Review"



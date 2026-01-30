# crm/management/commands/sync_inboxes.py

import email
import imaplib
import re
import locale
from email.header import decode_header
from email.utils import parseaddr

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crm.models import Lead
from crm.models_email import EmailThread, EmailMessage
from crm.models_email_config import EmailInboxConfig


# Safe locale for AWS EC2 (Amazon Linux)
try:
    locale.setlocale(locale.LC_ALL, "C.UTF-8")
except Exception:
    try:
        locale.setlocale(locale.LC_ALL, "C")
    except Exception:
        pass


FORM_FIELD_ALIASES = {
    "name": ["name", "contact name"],
    "email": ["email", "email address", "e mail"],
    "phone": ["phone", "phone number", "tel", "telephone"],
    "company": ["company", "company name", "business name", "brand name", "account or brand", "account brand"],
    "brand_stage": ["brand stage", "stage"],
    "product_interest": ["products looking for", "product interest", "products", "items", "product"],
    "order_quantity": ["order quantity", "quantity", "qty"],
    "preferred_time": ["preferred time our agent can call you", "preferred time", "call time", "preferred contact time"],
    "preferred_date": ["preferred date our agent can call you", "preferred date", "call date"],
    "notes": ["additional notes", "message", "notes", "additional note"],
}

FORM_SUBJECT_RE = re.compile(r"(?i)\bnew\s*form\s*entry\b")
FORM_BODY_RE = re.compile(r"(?i)\bnew\s*website\s*form\s*submission\b")


def _norm_enc(enc: str) -> str:
    e = (enc or "").strip().lower()
    if not e:
        return "utf-8"
    if e in ["windows-874", "windows874"]:
        return "cp874"
    if e in ["utf8", "utf-8"]:
        return "utf-8"
    return e


def _safe_decode_bytes(b: bytes, enc: str) -> str:
    if b is None:
        return ""
    use_enc = _norm_enc(enc)
    try:
        return b.decode(use_enc, errors="ignore")
    except Exception:
        try:
            return b.decode("utf-8", errors="ignore")
        except Exception:
            try:
                return b.decode("latin-1", errors="ignore")
            except Exception:
                return ""


def _decode(value) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for part, enc in parts:
        if isinstance(part, bytes):
            out.append(_safe_decode_bytes(part, enc or "utf-8"))
        else:
            out.append(str(part))
    return "".join(out).strip()


def _parse_addr(header_value: str):
    name, addr = parseaddr(header_value or "")
    name = _decode(name).strip()
    addr = (addr or "").strip().lower()
    return name, addr


def _get_text_and_html(msg):
    text = ""
    html = ""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue

            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            decoded = _safe_decode_bytes(payload, charset)

            if ctype == "text/plain" and not text:
                text = decoded
            elif ctype == "text/html" and not html:
                html = decoded
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        decoded = _safe_decode_bytes(payload, charset)
        if (msg.get_content_type() or "").lower() == "text/html":
            html = decoded
        else:
            text = decoded

    return (text or "").strip(), (html or "").strip()


def _html_to_text(html: str) -> str:
    h = html or ""
    h = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</p\s*>", "\n", h)
    h = re.sub(r"(?i)</div\s*>", "\n", h)
    h = re.sub(r"(?i)</li\s*>", "\n", h)
    h = re.sub(r"(?i)</ol\s*>", "\n", h)
    h = re.sub(r"(?i)</ul\s*>", "\n", h)
    h = re.sub(r"(?s)<.*?>", " ", h)

    h = h.replace("&nbsp;", " ")
    h = h.replace("&amp;", "&")
    h = h.replace("&lt;", "<")
    h = h.replace("&gt;", ">")
    h = h.replace("&#39;", "'")
    h = h.replace("&quot;", '"')

    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n\n", h)
    return h.strip()


def _extract_email_from_anywhere(text: str) -> str:
    m = re.search(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", text or "", re.I)
    return (m.group(1) if m else "").strip().lower()


def _clean_value(v: str) -> str:
    v = (v or "").strip()
    v = re.sub(r"(?i)^mailto:\s*", "", v).strip()
    v = re.sub(r"[<>\"']", "", v).strip()
    return v


def _is_placeholder_value(v: str) -> bool:
    x = (v or "").strip().lower()
    if not x:
        return True
    bad = {
        "name",
        "contact name",
        "contact_name",
        "email",
        "email address",
        "phone",
        "phone number",
        "account_brand",
        "account or brand",
        "company",
        "company name",
        "brand",
        "notes",
        "additional notes",
        "message",
        "brand stage",
        "products looking for",
        "order quantity",
        "preferred time",
        "preferred contact time",
        "upload design",
    }
    return x in bad


def _clean_parsed_value(v: str) -> str:
    v = _clean_value(v or "")
    if _is_placeholder_value(v):
        return ""
    return v


def _extract_form_entry_number(subject: str) -> str:
    s = subject or ""
    m = re.search(r"(?i)\blead\s*id\s*[:#]?\s*(\d+)\b", s)
    if m:
        return m.group(1)

    m = re.search(r"#\s*(\d+)\b", s)
    if m:
        return m.group(1)

    return ""


def _is_form_entry(subject: str, body_text: str, body_html: str) -> bool:
    s = (subject or "")
    t = (body_text or "")
    h = (body_html or "")

    # Strong signals
    if FORM_SUBJECT_RE.search(s) or FORM_BODY_RE.search(t) or FORM_BODY_RE.search(h):
        return True

    # Fallback signals
    text = (s + "\n" + t + "\n" + h).lower()
    form_signals = [
        "you have a new website form submission",
        "new website form submission",
        "new form entry",
        "website form submission",
    ]
    field_signals = [
        "email address",
        "phone",
        "brand stage",
        "products looking for",
        "order quantity",
        "additional notes",
        "name",
    ]

    has_form_signal = any(x in text for x in form_signals)
    has_field_signal = any(x in text for x in field_signals)

    return has_form_signal and has_field_signal


def _parse_form_fields(body_text: str, body_html: str) -> dict:
    src = (body_text or "").strip()
    if not src and body_html:
        src = _html_to_text(body_html)

    src = (src or "").replace("\r", "")
    lines = [ln.strip() for ln in src.split("\n") if ln.strip()]

    def find_after_any_label(alias_list):
        alias_list = [a.lower() for a in alias_list]

        def is_label_line(line_low: str, alias: str) -> bool:
            clean = line_low.strip()
            clean2 = clean.replace(" :", ":")
            if clean == alias:
                return True
            if clean2.startswith(alias + ":"):
                return True
            return False

        for i, ln in enumerate(lines):
            low = ln.lower()

            matched = None
            for a in alias_list:
                if is_label_line(low, a):
                    matched = a
                    break
            if not matched:
                continue

            if ":" in ln:
                right = ln.split(":", 1)[1].strip()
                right = _clean_parsed_value(right)
                if right:
                    return right

            if i + 1 < len(lines):
                return _clean_parsed_value(lines[i + 1].strip())

        return ""

    name = _clean_parsed_value(find_after_any_label(FORM_FIELD_ALIASES["name"]))
    email_addr = _clean_parsed_value(find_after_any_label(FORM_FIELD_ALIASES["email"]))
    phone = _clean_parsed_value(find_after_any_label(FORM_FIELD_ALIASES["phone"]))
    company = _clean_parsed_value(find_after_any_label(FORM_FIELD_ALIASES["company"]))

    brand_stage = _clean_parsed_value(find_after_any_label(FORM_FIELD_ALIASES["brand_stage"]))
    product_interest = _clean_parsed_value(find_after_any_label(FORM_FIELD_ALIASES["product_interest"]))
    order_quantity = _clean_parsed_value(find_after_any_label(FORM_FIELD_ALIASES["order_quantity"]))
    preferred_time = _clean_parsed_value(find_after_any_label(FORM_FIELD_ALIASES["preferred_time"]))
    preferred_date = _clean_parsed_value(find_after_any_label(FORM_FIELD_ALIASES["preferred_date"]))

    notes_main = find_after_any_label(FORM_FIELD_ALIASES["notes"])
    notes_main = _clean_value(notes_main)

    if not email_addr:
        email_addr = _clean_parsed_value(_extract_email_from_anywhere(src))
    email_addr = _clean_parsed_value(_extract_email_from_anywhere(email_addr) or email_addr)

    extra = []
    if brand_stage:
        extra.append(f"Brand Stage: {brand_stage}")
    if product_interest:
        extra.append(f"Products Looking For: {product_interest}")
    if order_quantity:
        extra.append(f"Order Quantity: {order_quantity}")
    if preferred_date:
        extra.append(f"Preferred Date: {preferred_date}")
    if preferred_time:
        extra.append(f"Preferred Time: {preferred_time}")

    notes_out = (notes_main or "").strip()
    if extra:
        extra_block = "\n".join(extra).strip()
        if notes_out:
            notes_out = (notes_out + "\n\n" + extra_block).strip()
        else:
            notes_out = extra_block

    return {
        "contact_name": (name or "")[:200],
        "email": (email_addr or "")[:255],
        "phone": (phone or "")[:50],
        "account_brand": (company or "")[:200],
        "product_interest": (product_interest or "")[:200],
        "order_quantity": (order_quantity or "")[:100],
        "preferred_contact_time": (preferred_time or "")[:100],
        "notes": notes_out,
    }


def _pick_non_empty(current: str, new: str) -> str:
    if (current or "").strip():
        return current
    return (new or "").strip()


def _append_notes(existing: str, extra: str) -> str:
    e = (existing or "").strip()
    x = (extra or "").strip()
    if not x:
        return e
    if not e:
        return x
    if x in e:
        return e
    return (e + "\n\n" + x).strip()


def _create_or_update_lead_from_form(parsed: dict, entry_no: str = ""):
    email_addr = (parsed.get("email") or "").strip().lower()
    name = (parsed.get("contact_name") or "").strip()
    brand = (parsed.get("account_brand") or "").strip()

    if not email_addr:
        return None, "skipped_no_email"

    lead = Lead.objects.filter(email__iexact=email_addr).first()

    desired_lead_id = ""
    if entry_no:
        desired_lead_id = f"L{entry_no}".strip()

    if lead:
        if desired_lead_id:
            clash = Lead.objects.filter(lead_id=desired_lead_id).exclude(id=lead.id).exists()
            if not clash:
                lead.lead_id = desired_lead_id

        lead.account_brand = _pick_non_empty(getattr(lead, "account_brand", ""), brand)
        lead.contact_name = _pick_non_empty(getattr(lead, "contact_name", ""), name)
        lead.phone = _pick_non_empty(getattr(lead, "phone", ""), parsed.get("phone", ""))

        if hasattr(lead, "product_interest"):
            lead.product_interest = _pick_non_empty(getattr(lead, "product_interest", ""), parsed.get("product_interest", ""))

        if hasattr(lead, "order_quantity"):
            lead.order_quantity = _pick_non_empty(getattr(lead, "order_quantity", ""), parsed.get("order_quantity", ""))

        lead.notes = _append_notes(getattr(lead, "notes", ""), parsed.get("notes", ""))
        lead.save()
        return lead, "updated_existing"

    candidate_id = desired_lead_id or None
    if candidate_id and Lead.objects.filter(lead_id=candidate_id).exists():
        candidate_id = None

    lead = Lead.objects.create(
        lead_id=candidate_id,
        market="CA",
        account_brand=brand,
        contact_name=name,
        email=email_addr,
        phone=(parsed.get("phone", "") or "").strip(),
        source="Website Inquiry",
        lead_type="Startup / New Brand",
        lead_status="New",
        priority="Medium",
        notes=(parsed.get("notes", "") or "").strip(),
    )

    return lead, "created_new"


def _thread_subject_key(subject: str) -> str:
    s = (subject or "").strip()
    if not s:
        return ""
    m = re.search(r"(?i)(new form entry\s*#\s*\d+)", s)
    if m:
        return m.group(1).strip()
    s2 = re.sub(r"(?i)^(re|fw|fwd)\s*:\s*", "", s).strip()
    return s2[:255]


class Command(BaseCommand):
    help = "Sync lead and info inboxes via IMAP and store messages. Creates Leads from form entry emails."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--backfill", action="store_true")

    def handle(self, *args, **opts):
        limit = int(opts.get("limit") or 200)
        do_backfill = bool(opts.get("backfill"))

        def get_cfg(label: str):
            obj = EmailInboxConfig.objects.filter(label=label, is_enabled=True).first()
            if not obj:
                return None
            return {
                "label": label,
                "imap_host": (obj.imap_host or "").strip(),
                "imap_port": int(obj.imap_port or 993),
                "username": (obj.username or "").strip(),
                "password": (obj.password or "").strip(),
                "use_ssl": bool(getattr(obj, "use_ssl", True)),
            }

        for label in ["lead", "info"]:
            inbox = get_cfg(label)
            if not inbox:
                self.stdout.write(self.style.WARNING(f"Skip {label}: missing config or disabled"))
                continue

            host = inbox["imap_host"]
            port = inbox["imap_port"]
            user = inbox["username"]
            pw = inbox["password"]
            use_ssl = inbox["use_ssl"]

            if not (host and port and user and pw):
                self.stdout.write(self.style.WARNING(f"Skip {label}: missing host or user or password"))
                continue

            self.stdout.write(f"Syncing {label} ({user}) ...")

            im = None
            try:
                im = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
                im.login(user, pw)
                im.select("INBOX")

                typ, data = im.uid("search", None, "ALL")
                if typ != "OK":
                    self.stdout.write(self.style.WARNING(f"{label}: search failed"))
                    continue

                uids = (data[0] or b"").split()
                uids = uids[-limit:]

                for uid in uids:
                    uid_str = uid.decode("utf-8", errors="ignore").strip()
                    if not uid_str:
                        continue

                    typ, msg_data = im.uid("fetch", uid, "(RFC822)")
                    if typ != "OK" or not msg_data:
                        continue

                    raw = None
                    for item in msg_data:
                        if isinstance(item, tuple) and len(item) == 2:
                            raw = item[1]
                            break
                    if not raw:
                        continue

                    msg = email.message_from_bytes(raw)

                    subject = _decode(msg.get("Subject"))
                    from_full = _decode(msg.get("From"))
                    reply_to_full = _decode(msg.get("Reply-To"))
                    to_full = _decode(msg.get("To"))

                    from_name, from_email = _parse_addr(from_full)
                    rt_name, rt_email = _parse_addr(reply_to_full)

                    body_text, body_html = _get_text_and_html(msg)
                    if (not body_text) and body_html:
                        body_text = _html_to_text(body_html)

                    is_form = _is_form_entry(subject, body_text, body_html)
                    parsed = _parse_form_fields(body_text, body_html) if is_form else {}
                    entry_no = _extract_form_entry_number(subject) if is_form else ""

                    if is_form:
                        if parsed.get("email"):
                            from_email = parsed["email"]
                        elif rt_email:
                            from_email = rt_email
                            from_name = rt_name or from_name

                        if parsed.get("contact_name"):
                            from_name = parsed["contact_name"]

                    subject_key = _thread_subject_key(subject)

                    thread, _ = EmailThread.objects.get_or_create(
                        label=label,
                        mailbox=user,
                        subject=subject_key[:255],
                        defaults={
                            "from_email": (from_email or "")[:255],
                            "from_name": (from_name or "")[:255],
                            "last_message_at": timezone.now(),
                        },
                    )

                    existing = EmailMessage.objects.filter(thread=thread, imap_uid=uid_str).first()
                    if existing and not do_backfill:
                        continue

                    if existing and do_backfill:
                        changed = False

                        if (not (existing.body_text or "").strip()) and (body_text or "").strip():
                            existing.body_text = body_text
                            changed = True
                        if (not (existing.body_html or "").strip()) and (body_html or "").strip():
                            existing.body_html = body_html
                            changed = True

                        if is_form:
                            if not existing.is_form_entry:
                                existing.is_form_entry = True
                                changed = True
                            if not existing.is_lead_candidate:
                                existing.is_lead_candidate = True
                                changed = True

                        if changed:
                            existing.save()

                        if is_form and parsed:
                            _create_or_update_lead_from_form(parsed, entry_no)

                        continue

                    with transaction.atomic():
                        EmailMessage.objects.create(
                            thread=thread,
                            imap_uid=uid_str,
                            subject=(subject or "")[:255],
                            from_email=(from_email or "")[:255],
                            from_name=(from_name or "")[:255],
                            to_email=(to_full or "")[:255],
                            body_text=body_text,
                            body_html=body_html,
                            is_form_entry=is_form,
                            is_lead_candidate=is_form,
                        )

                        if is_form and parsed:
                            lead, status = _create_or_update_lead_from_form(parsed, entry_no)
                            if entry_no:
                                self.stdout.write(f"Form entry #{entry_no}: lead {status}")

                    thread.last_message_at = timezone.now()
                    thread.from_email = (from_email or "")[:255]
                    thread.from_name = (from_name or "")[:255]
                    thread.save(update_fields=["last_message_at", "from_email", "from_name"])

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"{label}: sync failed: {str(e)[:220]}"))
            finally:
                try:
                    if im:
                        im.logout()
                except Exception:
                    pass

        self.stdout.write(self.style.SUCCESS("Done."))
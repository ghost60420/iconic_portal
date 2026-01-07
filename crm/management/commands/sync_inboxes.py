# crm/management/commands/sync_inboxes.py

import imaplib
import email
import re
from email.header import decode_header

from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Lead
from crm.models_email import EmailThread, EmailMessage
from crm.models_email_config import EmailInboxConfig


FORM_FIELD_ALIASES = {
    "name": ["name"],
    "email": ["email", "email address", "e mail"],
    "phone": ["phone", "phone number", "tel", "telephone"],
    "company": ["company", "company name", "brand", "account", "account brand"],
    "notes": ["additional notes", "message", "notes", "additional note"],
}


def _norm_enc(enc: str) -> str:
    e = (enc or "").strip().lower()
    if not e:
        return "utf-8"
    if e in ["windows-874", "windows874"]:
        return "cp874"
    if e in ["utf8", "utf-8"]:
        return "utf-8"
    # unknown enc names can crash decode, so keep safe fallback later
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


def _parse_email_from_header(header_value: str):
    v = (header_value or "").strip()
    if not v:
        return "", ""
    if "<" in v and ">" in v:
        name = v.split("<")[0].strip().strip('"')
        addr = v.split("<")[1].split(">")[0].strip()
        return name, addr
    return "", v


def _get_text_and_html(msg):
    text = ""
    html = ""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
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
        if msg.get_content_type() == "text/html":
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


def _extract_form_entry_number(subject: str) -> str:
    m = re.search(r"#\s*(\d+)", subject or "")
    return m.group(1) if m else ""

def _is_form_entry(subject: str, body_text: str, body_html: str) -> bool:
    s = (subject or "").lower()
    t = (body_text or "").lower()
    h = (body_html or "").lower()

    if "new form entry" in s:
        return True
    if "website form submission" in s:
        return True
    if "website form submission" in t:
        return True
    if "website form submission" in h:
        return True
    if "new website form submission" in t:
        return True
    if "new website form submission" in h:
        return True
    return False

def _extract_email_from_anywhere(text: str) -> str:
    m = re.search(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", text or "", re.I)
    return (m.group(1) if m else "").strip()


def _clean_value(v: str) -> str:
    v = (v or "").strip()
    # remove common wrappers
    v = re.sub(r"(?i)^mailto:\s*", "", v).strip()
    v = re.sub(r"[<>\"']", "", v).strip()
    return v


def _parse_form_fields(body_text: str, body_html: str) -> dict:
    src = (body_text or "").strip()
    if not src and body_html:
        src = _html_to_text(body_html)

    src = (src or "").replace("\r", "")
    lines = [ln.strip() for ln in src.split("\n") if ln.strip()]

    def find_after_any_label(alias_list):
        alias_list = [a.lower() for a in alias_list]
        for i, ln in enumerate(lines):
            low = ln.lower()

            if any(a in low for a in alias_list):
                # "Email Address: test@test.com"
                if ":" in ln:
                    right = ln.split(":", 1)[1].strip()
                    right = _clean_value(right)
                    if right:
                        return right

                # label then next line
                if i + 1 < len(lines):
                    return _clean_value(lines[i + 1].strip())
        return ""

    name = find_after_any_label(FORM_FIELD_ALIASES["name"])
    email_addr = find_after_any_label(FORM_FIELD_ALIASES["email"])
    phone = find_after_any_label(FORM_FIELD_ALIASES["phone"])
    company = find_after_any_label(FORM_FIELD_ALIASES["company"])
    notes = find_after_any_label(FORM_FIELD_ALIASES["notes"])

    if not email_addr:
        email_addr = _extract_email_from_anywhere(src)

    # normalize email if it has extra text
    email_addr = _extract_email_from_anywhere(email_addr) or email_addr

    return {
        "contact_name": (name or "")[:255],
        "email": (email_addr or "")[:255],
        "phone": (phone or "")[:50],
        "account_brand": (company or "")[:255],
        "notes": notes or "",
    }


def _create_or_update_lead_from_form(entry_no: str, parsed: dict):
    if not entry_no:
        return None, "missing_entry_no"

    lead_id_value = str(entry_no).strip()

    lead = Lead.objects.filter(lead_id=lead_id_value).first()
    if lead:
        changed = False

        if parsed.get("contact_name") and not (lead.contact_name or "").strip():
            lead.contact_name = parsed["contact_name"]
            changed = True
        if parsed.get("email") and not (lead.email or "").strip():
            lead.email = parsed["email"]
            changed = True
        if parsed.get("phone") and not (lead.phone or "").strip():
            lead.phone = parsed["phone"]
            changed = True
        if parsed.get("account_brand") and not (lead.account_brand or "").strip():
            lead.account_brand = parsed["account_brand"]
            changed = True

        if parsed.get("notes"):
            existing = (lead.notes or "").strip()
            if parsed["notes"] and parsed["notes"] not in existing:
                lead.notes = (existing + "\n\n" + parsed["notes"]).strip() if existing else parsed["notes"]
                changed = True

        if changed:
            lead.save()

        return lead, "updated_existing"

    lead = Lead.objects.create(
        lead_id=lead_id_value,
        market="CA",
        account_brand=parsed.get("account_brand", ""),
        contact_name=parsed.get("contact_name", ""),
        email=parsed.get("email", ""),
        phone=parsed.get("phone", ""),
        source="Website form email",
        lead_type="Website",
        lead_status="New",
        priority="Normal",
        notes=parsed.get("notes", ""),
        created_date=timezone.now(),
    )
    return lead, "created"


def _thread_subject_key(subject: str) -> str:
    """
    Reduce duplicate threads.
    For form emails we group by 'New Form Entry #1234'.
    For others we group by clean subject.
    """
    s = (subject or "").strip()
    if not s:
        return ""
    m = re.search(r"(?i)(new form entry\s*#\s*\d+)", s)
    if m:
        return m.group(1).strip()
    # remove common prefixes
    s2 = re.sub(r"(?i)^(re|fw|fwd)\s*:\s*", "", s).strip()
    return s2[:255]


class Command(BaseCommand):
    help = "Sync lead and info inboxes via IMAP and store messages. Creates Leads from form entry emails."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument(
            "--backfill",
            action="store_true",
            help="Reparse existing saved messages to fill body_text and fix missing Lead fields.",
        )

    def handle(self, *args, **opts):
        limit = int(opts["limit"] or 200)
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

                    from_name, from_email = _parse_email_from_header(from_full)
                    rt_name, rt_email = _parse_email_from_header(reply_to_full)

                    body_text, body_html = _get_text_and_html(msg)

                    # ALWAYS keep text filled, even if email is html only
                    if (not body_text) and body_html:
                        body_text = _html_to_text(body_html)

                    is_form = _is_form_entry(subject, body_text, body_html)
                    if not is_form:
                        maybe = _parse_form_fields(body_text, body_html)
                        if maybe.get("email") or maybe.get("phone") or maybe.get("contact_name"):
                            is_form = True
                            parsed = maybe
                    entry_no = _extract_form_entry_number(subject) if is_form else ""

                    parsed = {}
                    if is_form:
                        parsed = _parse_form_fields(body_text, body_html)

                        # real sender often in body
                        if parsed.get("email"):
                            from_email = parsed["email"]
                        elif rt_email:
                            from_email = rt_email
                            from_name = rt_name or from_name

                        if parsed.get("contact_name"):
                            from_name = parsed["contact_name"]

                    subject_key = _thread_subject_key(subject)

                    # IMPORTANT: use subject_key to reduce duplicates
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

                        # fill missing bodies
                        if (not (existing.body_text or "").strip()) and (body_text or "").strip():
                            existing.body_text = body_text
                            changed = True
                        if (not (existing.body_html or "").strip()) and (body_html or "").strip():
                            existing.body_html = body_html
                            changed = True

                        # for form emails fix sender
                        if is_form:
                            if parsed.get("email") and (existing.from_email or "").lower().endswith("@iconicapparelhouse.com"):
                                existing.from_email = parsed["email"][:255]
                                changed = True
                            if parsed.get("contact_name") and not (existing.from_name or "").strip():
                                existing.from_name = parsed["contact_name"][:255]
                                changed = True

                        if changed:
                            existing.save()

                        # backfill lead linkage and lead missing fields
                        if is_form and entry_no:
                            lead, _ = _create_or_update_lead_from_form(entry_no, parsed)

                            if hasattr(existing, "lead") and lead and not getattr(existing, "lead_id", None):
                                existing.lead = lead
                                existing.save(update_fields=["lead"])

                        continue

                    # Create new message
                    msg_obj = EmailMessage.objects.create(
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

                    if is_form and entry_no:
                        lead, status = _create_or_update_lead_from_form(entry_no, parsed)

                        if hasattr(msg_obj, "lead") and lead:
                            msg_obj.lead = lead
                            msg_obj.save(update_fields=["lead"])

                        self.stdout.write(f"Form entry #{entry_no}: lead {status}")

                    # keep thread updated
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
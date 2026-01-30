import email
import imaplib
import re
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from crm.models import InboundEmail, MailboxSyncState, Lead, EmailLeadLink


def _decode_header(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="ignore"))
            except Exception:
                out.append(text.decode("utf-8", errors="ignore"))
        else:
            out.append(str(text))
    return "".join(out).strip()


def _get_text_body(msg) -> str:
    if msg.is_multipart():
        best = ""
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue

            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"

            if ctype == "text/plain":
                return payload.decode(charset, errors="ignore").strip()

            if ctype == "text/html" and not best:
                html = payload.decode(charset, errors="ignore")
                best = re.sub(r"<[^>]+>", " ", html)
                best = re.sub(r"\s+", " ", best).strip()

        return best.strip()

    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="ignore").strip()


def looks_like_lead(subject: str, body: str) -> bool:
    text = (subject + " " + body).lower()
    keys = [
        "quote", "pricing", "sample", "moq", "manufacturer", "manufacturing",
        "hoodie", "t shirt", "tshirt", "activewear", "swimwear",
        "tech pack", "factory", "production", "order quantity",
        "private label", "bulk"
    ]
    return any(k in text for k in keys)


def _pick(text: str, patterns) -> str:
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return (m.group(1) or "").strip()
    return ""


def parse_lead_from_email(subject: str, body: str, from_email: str, from_name: str) -> dict:
    body = (body or "").strip()
    subject = (subject or "").strip()
    from_email = (from_email or "").strip().lower()
    from_name = (from_name or "").strip()

    data = {
        "account_brand": "",
        "contact_name": "",
        "email": from_email,
        "phone": "",

        "contact2_name": "",
        "email2": "",
        "phone2": "",

        "company_website": "",
        "country": "",
        "city": "",
        "product_interest": "",
        "order_quantity": "",
        "budget": "",
        "preferred_contact_time": "",
        "notes": "",
    }

    text = body

    data["contact_name"] = _pick(text, [
        r"^\s*name\s*:\s*(.+)$",
        r"^\s*contact name\s*:\s*(.+)$",
        r"^\s*full name\s*:\s*(.+)$",
    ])

    data["account_brand"] = _pick(text, [
        r"^\s*brand\s*:\s*(.+)$",
        r"^\s*company\s*:\s*(.+)$",
        r"^\s*account\s*:\s*(.+)$",
    ])

    data["phone"] = _pick(text, [
        r"^\s*phone\s*:\s*(.+)$",
        r"^\s*mobile\s*:\s*(.+)$",
        r"^\s*whatsapp\s*:\s*(.+)$",
    ])

    data["company_website"] = _pick(text, [
        r"^\s*website\s*:\s*(.+)$",
        r"^\s*site\s*:\s*(.+)$",
    ])

    data["country"] = _pick(text, [r"^\s*country\s*:\s*(.+)$"])
    data["city"] = _pick(text, [r"^\s*city\s*:\s*(.+)$"])

    data["product_interest"] = _pick(text, [
        r"^\s*products?\s*looking\s*for\s*:\s*(.+)$",
        r"^\s*product\s*interest\s*:\s*(.+)$",
        r"^\s*items\s*:\s*(.+)$",
    ])

    data["order_quantity"] = _pick(text, [
        r"^\s*order\s*quantity\s*:\s*(.+)$",
        r"^\s*quantity\s*:\s*(.+)$",
        r"^\s*qty\s*:\s*(.+)$",
    ])

    data["budget"] = _pick(text, [r"^\s*budget\s*:\s*(.+)$"])

    data["preferred_contact_time"] = _pick(text, [
        r"^\s*preferred\s*contact\s*time\s*:\s*(.+)$",
        r"^\s*preferred\s*time\s*:\s*(.+)$",
    ])

    data["contact2_name"] = _pick(text, [
        r"^\s*second\s*contact\s*name\s*:\s*(.+)$",
        r"^\s*contact\s*2\s*name\s*:\s*(.+)$",
    ])

    data["email2"] = _pick(text, [
        r"^\s*second\s*email\s*:\s*(.+)$",
        r"^\s*email\s*2\s*:\s*(.+)$",
    ])

    data["phone2"] = _pick(text, [
        r"^\s*second\s*phone\s*:\s*(.+)$",
        r"^\s*phone\s*2\s*:\s*(.+)$",
    ])

    if not data["contact_name"]:
        data["contact_name"] = from_name or from_email or "Unknown"

    if not data["account_brand"]:
        data["account_brand"] = "Unknown"

    notes_parts = []
    if subject:
        notes_parts.append(f"Subject: {subject}")
    if from_email:
        notes_parts.append(f"From: {from_email}")
    if text:
        notes_parts.append("")
        notes_parts.append(text[:4000])

    data["notes"] = "\n".join(notes_parts).strip()
    return data


def _find_or_create_lead(from_email: str, from_name: str, subject: str, body: str, default_source: str) -> Lead:
    from_email = (from_email or "").strip().lower()
    lead = None

    if from_email:
        lead = Lead.objects.filter(email__iexact=from_email).first()
        if lead:
            return lead

    parsed = parse_lead_from_email(subject, body, from_email, from_name)

    lead = Lead.objects.create(
        account_brand=parsed["account_brand"] or "Unknown",
        contact_name=parsed["contact_name"] or "Unknown",
        email=parsed["email"] or "",
        phone=parsed["phone"] or "",

        contact2_name=parsed["contact2_name"] or "",
        email2=parsed["email2"] or "",
        phone2=parsed["phone2"] or "",

        company_website=parsed["company_website"] or "",
        country=parsed["country"] or "",
        city=parsed["city"] or "",
        product_interest=parsed["product_interest"] or "",
        order_quantity=parsed["order_quantity"] or "",
        budget=parsed["budget"] or "",
        preferred_contact_time=parsed["preferred_contact_time"] or "",

        source=default_source,
        lead_type="Startup / New Brand",
        lead_status="New",
        priority="Medium",

        notes=parsed["notes"] or "",
    )
    return lead


def sync_mailbox(mailbox_key: str) -> dict:
    cfg = settings.EMAIL_SYNC.get(mailbox_key)
    if not cfg:
        return {"ok": False, "error": "Missing mailbox config"}

    label = cfg["label"]
    host = cfg["imap_host"]
    port = int(cfg.get("imap_port", 993))
    user = cfg["username"]
    pw = cfg["password"]
    use_ssl = bool(cfg.get("use_ssl", True))

    state, _ = MailboxSyncState.objects.get_or_create(mailbox_label=label)
    last_uid = int(state.last_uid or 0)

    imap = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
    imap.login(user, pw)
    imap.select("INBOX")

    typ, data = imap.uid("search", None, f"(UID {last_uid + 1}:*)")
    if typ != "OK":
        imap.logout()
        return {"ok": False, "error": "IMAP search failed"}

    uids = [x for x in (data[0] or b"").split() if x]
    created = 0
    flagged = 0
    linked = 0
    max_uid_seen = last_uid

    for uid_b in uids:
        uid = int(uid_b)
        if uid > max_uid_seen:
            max_uid_seen = uid

        typ, msgdata = imap.uid("fetch", uid_b, "(RFC822)")
        if typ != "OK" or not msgdata or not msgdata[0]:
            continue

        raw = msgdata[0][1]
        msg = email.message_from_bytes(raw)

        message_id = (msg.get("Message-ID") or "").strip()
        subject = _decode_header(msg.get("Subject") or "")
        from_name, from_email = parseaddr(msg.get("From") or "")
        from_name = _decode_header(from_name)
        to_email = parseaddr(msg.get("To") or "")[1]

        body = _get_text_body(msg)

        dt = None
        try:
            dt = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
        except Exception:
            dt = None
        if dt and timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        received_at = dt or timezone.now()

        try:
            with transaction.atomic():
                obj, is_new = InboundEmail.objects.get_or_create(
                    mailbox_label=label,
                    uid=uid,
                    defaults={
                        "from_email": (from_email or "").lower(),
                        "from_name": from_name or "",
                        "to_email": (to_email or "").lower(),
                        "subject": subject[:255],
                        "body_text": body,
                        "received_at": received_at,
                        "message_id": message_id[:255],
                    },
                )
                if not is_new:
                    continue

                created += 1

                if label == "lead":
                    if obj.from_email:
                        lead = _find_or_create_lead(
                            from_email=obj.from_email,
                            from_name=obj.from_name,
                            subject=obj.subject,
                            body=obj.body_text,
                            default_source="Email Campaign",
                        )
                        EmailLeadLink.objects.get_or_create(email=obj, lead=lead)
                        linked += 1

                if label == "info":
                    if looks_like_lead(obj.subject, obj.body_text):
                        flagged += 1
                        if obj.from_email:
                            lead = _find_or_create_lead(
                                from_email=obj.from_email,
                                from_name=obj.from_name,
                                subject=obj.subject,
                                body=obj.body_text,
                                default_source="Email Campaign",
                            )
                            EmailLeadLink.objects.get_or_create(email=obj, lead=lead)
                            linked += 1

        except Exception:
            continue

    state.last_uid = max_uid_seen
    state.save(update_fields=["last_uid", "updated_at"])
    imap.logout()

    return {
        "ok": True,
        "mailbox": label,
        "created": created,
        "linked": linked,
        "flagged": flagged,
        "last_uid": max_uid_seen,
    }
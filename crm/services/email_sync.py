import email
import imaplib
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime

from django.conf import settings
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
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="ignore").strip()
        return ""
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


def _find_or_create_lead(from_email: str, from_name: str, body: str) -> Lead:
    lead = Lead.objects.filter(email__iexact=from_email).first()
    if lead:
        return lead

    lead = Lead.objects.create(
        contact_name=from_name or from_email,
        email=from_email,
        notes=(body[:2000] if body else ""),
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
                lead = _find_or_create_lead(obj.from_email, obj.from_name, obj.body_text)
                EmailLeadLink.objects.get_or_create(email=obj, lead=lead)
                linked += 1

        if label == "info":
            if looks_like_lead(obj.subject, obj.body_text):
                flagged += 1
                if obj.from_email:
                    lead = _find_or_create_lead(obj.from_email, obj.from_name, obj.body_text)
                    EmailLeadLink.objects.get_or_create(email=obj, lead=lead)
                    linked += 1

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
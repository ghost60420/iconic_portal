import imaplib
import email
import re
from email.header import decode_header
from crm.models_email_sync import EmailInboxConfig
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from crm.models import Lead
from crm.models_email import EmailThread, EmailMessage


def _decode(s):
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for p, enc in parts:
        if isinstance(p, bytes):
            out.append(p.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(str(p))
    return "".join(out)


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
            decoded = payload.decode(charset, errors="ignore")
            if ctype == "text/plain" and not text:
                text = decoded
            if ctype == "text/html" and not html:
                html = decoded
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="ignore")
        if msg.get_content_type() == "text/html":
            html = decoded
        else:
            text = decoded
    return (text or "").strip(), (html or "").strip()


def _extract_form_entry_number(subject: str) -> str:
    """
    "New Form Entry #952 for Contact Form" -> "952"
    """
    s = subject or ""
    m = re.search(r"#\s*(\d+)", s)
    return m.group(1) if m else ""


def _is_form_entry(subject, body_text):
    subject_l = (subject or "").lower()
    body_l = (body_text or "").lower()

    keys = getattr(settings, "EMAIL_MONITOR", {}).get("form_subject_contains", [])
    if any(k.lower() in subject_l for k in keys):
        return True

    if "new form entry" in subject_l:
        return True

    if "website form submission" in body_l:
        return True

    return False


def _is_lead_candidate(subject, body_text):
    subject_l = (subject or "").lower()
    body_l = (body_text or "").lower()
    keys = getattr(settings, "EMAIL_MONITOR", {}).get("sale_keywords", [])
    keys_l = [k.lower() for k in keys]
    return any(k in subject_l for k in keys_l) or any(k in body_l for k in keys_l)


def _parse_form_fields(body_text: str) -> dict:
    """
    Best effort parse for your form email format.
    Works with:
      1. Name
         Callie Derouard
      2. Email Address
         babeandbloomco@gmail.com
      3. Phone
         807...
      4. Company Name
         Babe & Bloom Co.
      5. Additional Notes
         ...
    """
    t = (body_text or "").replace("\r", "")
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]

    def find_value_after(label_words):
        label_words = [w.lower() for w in label_words]
        for i, ln in enumerate(lines):
            low = ln.lower()
            if all(w in low for w in label_words):
                # value is usually next line
                if i + 1 < len(lines):
                    return lines[i + 1].strip()
        return ""

    name = find_value_after(["name"])
    email_addr = find_value_after(["email"])
    phone = find_value_after(["phone"])
    company = find_value_after(["company"])
    notes = find_value_after(["additional", "notes"])

    # Some emails have "Additional Notes" and then multiple lines. Capture more if possible.
    if notes:
        try:
            idx = next(i for i, ln in enumerate(lines) if "additional" in ln.lower() and "notes" in ln.lower())
            collected = []
            for j in range(idx + 1, min(idx + 8, len(lines))):
                collected.append(lines[j])
            notes = "\n".join(collected).strip()
        except Exception:
            pass

    return {
        "contact_name": name[:255],
        "email": email_addr[:255],
        "phone": phone[:50],
        "account_brand": company[:255],
        "notes": notes,
    }


def _create_or_update_lead_from_form(*, entry_no: str, parsed: dict):
    """
    IMPORTANT RULE:
    lead_id MUST equal the form entry number.
    """
    if not entry_no:
        return None, "missing_entry_no"

    lead_id_value = str(entry_no).strip()

    lead = Lead.objects.filter(lead_id=lead_id_value).first()
    if lead:
        # Fill missing fields only
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
            if parsed["notes"] not in existing:
                lead.notes = (existing + "\n\n" + parsed["notes"]).strip() if existing else parsed["notes"]
                changed = True

        if changed:
            lead.save()
        return lead, "updated_existing"

    # Create new lead
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
        priority="High" if lead_id_value == "952" else "Normal",
        notes=parsed.get("notes", ""),
        created_date=timezone.now(),
    )
    return lead, "created"


class Command(BaseCommand):
    help = "Sync lead@ and info@ inboxes via IMAP and store messages in DB. Also creates Leads from New Form Entry emails."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **opts):
        limit = int(opts["limit"] or 50)

        # Monitor config (keywords etc)
        monitor_cfg = getattr(settings, "EMAIL_MONITOR", {}) or {}

        # Read inbox config from Admin table (recommended)
        # Model name assumed: EmailInboxConfig(label, enabled, imap_host, imap_port, username, use_ssl)
        try:
            from crm.models_email_sync import EmailInboxConfig
        except Exception:
            EmailInboxConfig = None

        if EmailInboxConfig is None:
            self.stdout.write(self.style.ERROR("Missing EmailInboxConfig model. Create it first if you want Admin based config."))
            return

        # Passwords come from env only (safe)
        pw_map = getattr(settings, "EMAIL_SYNC_PASSWORDS", {}) or {}

        def get_cfg(label: str):
            obj = EmailInboxConfig.objects.filter(label=label, enabled=True).first()
            if not obj:
                return None

            password = (pw_map.get(label) or "").strip()
            return {
                "label": label,
                "imap_host": (obj.imap_host or "").strip(),
                "imap_port": int(obj.imap_port or 993),
                "username": (obj.username or "").strip(),
                "password": password,
                "use_ssl": bool(getattr(obj, "use_ssl", True)),
            }

        for label in ["lead", "info"]:
            inbox = get_cfg(label)
            if not inbox:
                self.stdout.write(self.style.WARNING(f"Skip {label}: missing admin config or disabled"))
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
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    subject = _decode(msg.get("Subject"))
                    from_full = _decode(msg.get("From"))
                    to_full = _decode(msg.get("To"))

                    from_email = ""
                    from_name = ""
                    if "<" in from_full and ">" in from_full:
                        from_name = from_full.split("<")[0].strip().strip('"')
                        from_email = from_full.split("<")[1].split(">")[0].strip()
                    else:
                        from_email = (from_full or "").strip()

                    body_text, body_html = _get_text_and_html(msg)

                    # Thread: keep separate by label and mailbox and subject
                    thread, _ = EmailThread.objects.get_or_create(
                        label=label,
                        mailbox=user,
                        subject=subject[:255],
                        defaults={
                            "from_email": from_email[:255],
                            "from_name": from_name[:255],
                            "last_message_at": timezone.now(),
                        },
                    )

                    # Skip if already imported
                    if EmailMessage.objects.filter(thread=thread, imap_uid=uid_str).exists():
                        continue

                    is_form = _is_form_entry(subject, body_text)
                    is_lead = _is_lead_candidate(subject, body_text)

                    # Entry number (example 952)
                    entry_no = _extract_form_entry_number(subject) if is_form else ""

                    msg_obj = EmailMessage.objects.create(
                        thread=thread,
                        imap_uid=uid_str,
                        subject=subject[:255],
                        from_email=from_email[:255],
                        from_name=from_name[:255],
                        to_email=(to_full or "")[:255],
                        body_text=body_text,
                        body_html=body_html,
                        is_form_entry=is_form,
                        is_lead_candidate=is_lead,
                    )

                    # Create or update lead based on form entry number
                    if is_form and entry_no:
                        parsed = _parse_form_fields(body_text)
                        lead, status = _create_or_update_lead_from_form(entry_no=entry_no, parsed=parsed)

                        # Attach lead if EmailMessage has lead FK
                        if hasattr(msg_obj, "lead") and lead:
                            msg_obj.lead = lead
                            msg_obj.save(update_fields=["lead"])

                        self.stdout.write(f"Form entry #{entry_no}: lead {status}")

                    # Update thread
                    thread.last_message_at = timezone.now()
                    thread.from_email = from_email[:255]
                    thread.from_name = from_name[:255]
                    thread.save(update_fields=["last_message_at", "from_email", "from_name"])

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"{label}: sync failed: {str(e)[:200]}"))
            finally:
                try:
                    if im:
                        im.logout()
                except Exception:
                    pass

        self.stdout.write(self.style.SUCCESS("Done."))
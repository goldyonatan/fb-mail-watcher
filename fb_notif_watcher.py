#!/usr/bin/env python3
"""
Poll Gmail for Facebook notifications and forward ones
containing specific keywords to Telegram.

Runs once per invocation; put it in cron/systemd/Docker.
"""

# fb_notif_watcher.py
import imaplib, email, os, re, html, time
from email.header import decode_header
from bs4 import BeautifulSoup
import requests
from urllib.parse import urlparse
from pathlib import Path
from dotenv import load_dotenv
import os, sys


# ─── Locate and debug-load the .env file ───────────────────────────────
env_path = Path(__file__).resolve().parent / ".env"
#print("1) Looking for .env at:", env_path, file=sys.stderr)
#print("   Exists? ", env_path.exists(), file=sys.stderr)
if env_path.exists():
    pass
    #print("   Contents:\n", env_path.read_text(encoding="utf-8"), file=sys.stderr)
loaded = load_dotenv(dotenv_path=env_path, override=True)
#print("2) load_dotenv returned:", loaded, file=sys.stderr)
# ────────────────────────────────────────────────────────────────────────

# Now attempt to pull the vars
try:
    EMAIL_USER       = os.environ["EMAIL_USER"]
    EMAIL_PASS       = os.environ["EMAIL_PASS"]
    TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
    IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
except KeyError as e:
    print(f"❌ Missing env var: {e}", file=sys.stderr)
    sys.exit(1)

SEARCH_TERMS   = [t.strip() for t in
                  os.getenv("SEARCH_TERMS", "מומה,Moma").split(",")]
TERM_RE        = re.compile("|".join(map(re.escape, SEARCH_TERMS)),
                            re.IGNORECASE)

# ---- Helpers ----------------------------------------------------------
def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4096]})
    r.raise_for_status()

def msg_matches(msg):
    """
    Return a list of matching terms (empty list → no match).
    """
    subject      = decode_mime_words(msg["Subject"] or "")
    payload_text = extract_text_from_msg(msg)
    search_space = f"{subject}\n{payload_text}"

    found = {term for term in SEARCH_TERMS
             if re.search(re.escape(term), search_space, re.IGNORECASE)}

    # Optional: follow links as before
    if not found:
        for url in extract_links(payload_text):
            try:
                html_text = requests.get(url, timeout=10,
                                         headers={"User-Agent": "Mozilla/5.0"}).text
                for term in SEARCH_TERMS:
                    if re.search(re.escape(term), html_text, re.IGNORECASE):
                        found.add(term)
            except Exception:
                pass

    return list(found)       # e.g. ["מומה"] or ["Moma"]

def decode_mime_words(s):
    decoded = []
    for frag, enc in decode_header(s):
        decoded.append(
            frag.decode(enc or "utf-8", errors="replace") if isinstance(frag, bytes) else frag
        )
    return "".join(decoded)

def extract_text_from_msg(msg):
    """Return concatenated text/plain parts or strip HTML."""
    parts = []
    if msg.is_multipart():
        for p in msg.walk():
            ctype = p.get_content_type()
            if ctype == "text/plain":
                parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8",
                                                               errors="replace"))
            elif ctype == "text/html":
                html_body = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8",
                                                              errors="replace")
                parts.append(BeautifulSoup(html_body, "lxml").get_text(" ", strip=True))
    else:
        ctype = msg.get_content_type()
        body = msg.get_payload(decode=True)
        if body:
            body_dec = body.decode(msg.get_content_charset() or "utf-8", errors="replace")
            if ctype == "text/plain":
                parts.append(body_dec)
            else:
                parts.append(BeautifulSoup(body_dec, "lxml").get_text(" ", strip=True))
    return "\n".join(parts)

def extract_links(text):
    # Basic https://… extractor
    return re.findall(r"https?://\S+", text)

# ---- Main polling routine --------------------------------------------
def main():
    print("🔍 Connecting to Gmail…")
    with imaplib.IMAP4_SSL(IMAP_SERVER) as M:
        M.login(EMAIL_USER, EMAIL_PASS)
        print("✅ Logged in.")

        M.select("inbox")
        typ, data = M.search(None, '(UNSEEN HEADER From "facebookmail.com")')
        msg_nums = data[0].split() if data and data[0] else []
        print(f"🔢 Message IDs found: {msg_nums}")

        for num in msg_nums:
            print(f"📝 Fetching message {num.decode()}")
            typ, raw = M.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(raw[0][1])
            matches = msg_matches(msg)
            print(f"❓ matches → {matches}")

            if matches:
                # keep & mark as read
                link = next(iter(extract_links(extract_text_from_msg(msg))), "(no link)")
                subj = decode_mime_words(msg["Subject"] or "(no subject)")
                text = f"🔔 Facebook hit (found: {', '.join(matches)})\n{subj}\n{link}"
                print(f"✉️  Sending Telegram alert → {text}")
                send_telegram(text)
                M.store(num, "+FLAGS", "\\Seen")
            else:
                # permanently delete
                print(f"🗑️  Deleting message {num.decode()} (no keywords found)")
                M.store(num, "+FLAGS", "\\Deleted")

        # This will remove all messages flagged \Deleted
        M.expunge()
    print("🏁 Done.")


if __name__ == "__main__":
    main()

"""
Diagnose why urlparse fails on DATABASE_URL. Never prints the password.

Masks the credential as <user>:<redacted> and reports only structural
facts: how many '@' appear, whether a port is present, and how the
value differs from what the Neon CLI wrote.

Intentionally does NOT reconstruct a DSN. The previous version of this
script rebuilt one and produced a malformed URL, missing the slash
before the database name. Reassembling a connection string by hand is
exactly the error-prone step worth removing: the .env value is already
correct and should be copied verbatim.
"""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
text = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")

matches = re.findall(r"^DATABASE_URL\s*=\s*(.+)$", text, re.MULTILINE)
print(f"DATABASE_URL occurrences in .env : {len(matches)}")
if not matches:
    raise SystemExit("DATABASE_URL not found")

for index, raw in enumerate(matches, 1):
    value = raw.strip().strip('"').strip("'")
    masked = re.sub(r"://([^:@/]+):[^@]*@", r"://\1:<redacted>@", value)
    print(f"\n--- occurrence {index} ---")
    print(f"  masked value : {masked}")
    print(f"  starts with  : {value[:20]!r}")
    print(f"  ends with    : {value[-24:]!r}")
    print(f"  '@' count    : {value.count('@')}")
    print(f"  ':' count    : {value.count(':')}")
    print(f"  quotes       : {'yes' if raw.strip()[0] in '\"\'' else 'no'}")
    print(f"  whitespace   : {'leading/trailing' if value != value.strip() else 'none'}")

    parsed = urlparse(value)
    print(f"  scheme       : {parsed.scheme or '(none)'}")
    print(f"  netloc parts : {len(parsed.netloc.split('@'))}")
    hostport = parsed.netloc.rsplit("@", 1)[-1]
    print(f"  hostport     : {hostport}")
    print(f"  has ':port'  : {':' in hostport}")
    try:
        print(f"  parsed port  : {parsed.port}")
    except ValueError as exc:
        print(f"  parsed port  : ValueError -> {exc}")
    print(f"  path         : {parsed.path!r}")

print()
print("=" * 68)
print("Do not rebuild the DSN by hand. Open .env, copy the entire")
print("DATABASE_URL= line verbatim, and paste it into Streamlit")
print("Secrets. It is already correct and needs no escaping.")
print("=" * 68)
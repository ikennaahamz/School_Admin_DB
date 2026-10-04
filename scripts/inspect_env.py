"""Report the Neon connection target without printing any secret.

Only the host, port, database name and username are shown. The password,
AWS keys and S3 endpoint are never read into the output.
"""

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

env = Path(__file__).resolve().parent.parent / ".env"
text = env.read_text(encoding="utf-8", errors="replace")


def get(key: str) -> str | None:
    m = re.search(rf"^{key}\s*=\s*(.+)$", text, re.MULTILINE)
    if not m:
        return None
    value = m.group(1).strip().strip('"').strip("'")
    return value or None


print("=== connection targets in .env (secrets redacted) ===")
for key in ("DATABASE_URL", "DATABASE_URL_UNPOOLED", "LOCAL_DATABASE_URL"):
    raw = get(key)
    if not raw:
        print(f"{key:24} not set")
        continue
    parsed = urlparse(raw)
    scheme_tail = "sslmode=require" if "sslmode=require" in raw else ""
    print(f"{key:24} {parsed.scheme}://{parsed.hostname}:"
          f"{parsed.port or 5432}/{parsed.path.lstrip('/')}"
          f"  user={parsed.username or '?'}  {scheme_tail}")

print()
print("=== routing ===")
target = get("DB_TARGET")
print(f"DB_TARGET = {target!r}")
if target == "local":
    print("-> the application will use LOCAL_DATABASE_URL, not Neon.")
else:
    print("-> the application will use DATABASE_URL (Neon).")

print()
print("=== other Neon-injected variables (values not shown) ===")
for key in ("NEON_BRANCH", "NEON_AUTH_BASE_URL", "NEON_AUTH_JWKS_URL",
            "AWS_REGION", "AWS_ENDPOINT_URL_S3",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
    present = get(key) is not None
    print(f"  {key:26} {'present' if present else 'absent'}")
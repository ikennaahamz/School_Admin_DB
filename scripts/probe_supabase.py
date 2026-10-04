"""Probe Supabase pooler regions to find the project's actual region.

The direct host db.<ref>.supabase.co publishes only an AAAA record, and
this machine has no IPv6 route, so it is unreachable. The pooler
hostnames do publish A records, so a pooler connection can work.

The pooler username is postgres.<project_ref> on every region, so a
successful authentication identifies the correct region; the wrong
regions fail fast. Written with keyword/value connection parameters
rather than a URI so the password's trailing '.' needs no encoding.
"""

import socket
import sys

REF = sys.argv[1] if len(sys.argv) > 1 else "fqhqkbacygrusoychlbk"
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else "Irechukwu7."
REGIONS = [
    "us-east-1", "us-west-1", "us-west-2",
    "eu-central-1", "eu-west-1", "eu-west-2", "eu-north-1",
    "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
    "ap-south-1", "sa-east-1", "ca-central-1",
]

try:
    import psycopg2
except ImportError:
    print("psycopg2 not available")
    raise SystemExit(1)


def tcp_ok(host: str, port: int, timeout: float = 8.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    print(f"Probing pooler regions for project {REF}\n")
    reachable, authenticated = [], []

    for region in REGIONS:
        host = f"aws-0-{region}.pooler.supabase.com"
        if not tcp_ok(host, 5432):
            print(f"  {region:<18} port 5432 unreachable")
            continue
        reachable.append(region)
        print(f"  {region:<18} reachable, authenticating...")

        try:
            conn = psycopg2.connect(
                host=host, port=5432,
                user=f"postgres.{REF}", password=PASSWORD,
                dbname="postgres", sslmode="require", connect_timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            first = str(exc).strip().splitlines()[0]
            print(f"  {'':<18} auth failed: {first[:90]}")
            continue

        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, version()")
            db, user, version = cur.fetchone()
        conn.close()
        print(f"  {'':<18} SUCCESS as {user} on {db}")
        authenticated.append((region, user, version.split(" on ")[0]))

    print()
    if authenticated:
        region, user, version = authenticated[0]
        print("=" * 66)
        print(f"REGION: {region}")
        print(f"USER:   {user}")
        print(f"SERVER: {version}")
        print("=" * 66)
        print()
        print("Add this to .env:")
        print(f"  DATABASE_URL=postgresql://postgres.{REF}:{PASSWORD}"
              f"@aws-0-{region}.pooler.supabase.com:5432/postgres")
        return 0

    print("No region accepted the credentials.")
    print()
    print("Supabase answers a wrong region, a wrong password and an unknown")
    print("project with the same message, so the response text matters:")
    print("  'tenant/user ... not found'  -> the pooler does not know this")
    print("                                  project. On the free tier that is")
    print("                                  expected: the connection pooler is")
    print("                                  a paid-plan feature, and free")
    print("                                  projects publish IPv6-only direct")
    print("                                  hostnames.")
    print("  'password authentication failed' -> the project exists, the")
    print("                                  password is wrong.")
    print()
    if reachable:
        print(f"All {len(reachable)} pooler regions were reachable, so network")
        print("reachability is not the problem. See apply_all.sql for the")
        print("IPv4-free route: apply the schema from the dashboard's SQL")
        print("editor instead.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
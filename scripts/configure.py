#!/usr/bin/env python3
"""Create deployment configuration without a shared password or default signing key."""
import argparse
import ipaddress
import re
import secrets
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--domain", required=True, help="Public hostname, e.g. ask.example.com")
parser.add_argument("--owner", default="站长")
parser.add_argument("--proxy-cidrs", default="172.31.250.2/32")
parser.add_argument("--web-port", type=int, default=8000)
args = parser.parse_args()
if not re.fullmatch(r"[a-zA-Z0-9.-]+", args.domain) or "." not in args.domain:
    parser.error("Use a hostname without https:// or a path")
if not args.owner or len(args.owner) > 40 or any(char in args.owner for char in '\r\n"\''):
    parser.error("Owner name must be 1–40 characters without newlines or quotes")
try:
    proxies = ",".join(str(ipaddress.ip_network(value.strip())) for value in args.proxy_cidrs.split(",") if value.strip())
except ValueError:
    parser.error("Use comma-separated IP networks for --proxy-cidrs")
if not 1 <= args.web_port <= 65535:
    parser.error("--web-port must be between 1 and 65535")
root = Path(__file__).resolve().parent.parent
path = root / ".env"
content = (root / ".env.example").read_text().replace("REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_50_CHARACTERS", secrets.token_urlsafe(64))
content = content.replace("ask.example.com", args.domain).replace("OWNER_NAME=站长", f"OWNER_NAME={args.owner}")
content = content.replace("SITE_TITLE=站长的匿名提问箱", f"SITE_TITLE={args.owner}的匿名提问箱")
content = content.replace("TRUSTED_PROXY_CIDRS=172.31.250.2/32", f"TRUSTED_PROXY_CIDRS={proxies}")
content += f"\nWEB_PORT={args.web_port}\n"
try:
    with path.open("x") as output:
        output.write(content)
except FileExistsError:
    parser.error(".env already exists; edit it in place, it has not been overwritten")
path.chmod(0o600)
print("Created .env. No owner account has been created; use createsuperuser after starting the app.")

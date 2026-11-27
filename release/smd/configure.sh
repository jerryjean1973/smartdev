#!/bin/bash
set -euo pipefail
exec python3 ./configure-managed-dns.py -i "$@"

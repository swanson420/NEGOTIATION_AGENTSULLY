#!/usr/bin/env bash
set -euo pipefail

python3 -m pytest tests/test_system_integration.py -v

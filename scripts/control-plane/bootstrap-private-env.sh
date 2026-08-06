#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

require_root
require_lifecycle_platform
exec /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" bootstrap

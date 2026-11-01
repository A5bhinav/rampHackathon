#!/usr/bin/env bash
# Apply every migration, in owner order, to $DATABASE_URL. Each file runs in one transaction.
#
#   _bootstrap  schemas, roles, grants          (shared)
#   audit       evidence chain                  (Abhi)   app depends on it
#   app         cases, holds, releases, plumbing (Abhi)
#   ledger      Trust Ledger                    (Inesh)  references app.vendors
#   voice, lab  calls, answers, scenarios       (Sami)   reference app.cases
#   mock_ramp   Ramp-shaped mock                (Abhi)   stands alone
#
# Folders that do not exist yet are skipped, so each owner can land theirs independently.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
: "${DATABASE_URL:?set DATABASE_URL}"

ORDER=(_bootstrap audit app ledger voice lab mock_ramp)
for schema in "${ORDER[@]}"; do
  dir="$ROOT/supabase/migrations/$schema"
  [[ -d "$dir" ]] || continue
  while IFS= read -r f; do
    echo "apply  $schema/$(basename "$f")"
    psql -X -q -v ON_ERROR_STOP=1 -1 "$DATABASE_URL" -f "$f"
  done < <(find "$dir" -maxdepth 1 -name '*.sql' | sort)
done

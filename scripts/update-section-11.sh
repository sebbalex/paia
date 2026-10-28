#!/usr/bin/env bash
# Refresh the vendored Section 11 protocol under the coach skill from upstream,
# bump the pin in its README, and check that every heading the skill cites
# still exists. Leaves the changes unstaged for review (`git diff --stat`).
#
# Usage: scripts/update-section-11.sh [ref]   (default: upstream HEAD)

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/.claude/skills/coach/references/section-11"
upstream="https://github.com/CrankAddict/section-11"
ref="${1:-HEAD}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

git clone -q "$upstream" "$tmp/s11"
git -C "$tmp/s11" checkout -q "$ref"
sha="$(git -C "$tmp/s11" rev-parse HEAD)"
date="$(git -C "$tmp/s11" log -1 --format=%cs)"
version="$(grep -m1 -oE 'Protocol Version:\*\* *[0-9.]+' "$tmp/s11/SECTION_11.md" | grep -oE '[0-9.]+$')"

# upstream path -> vendored path. Same subset as first vendored; add a line
# here to vendor another file.
while read -r src dst; do
  install -m 644 "$tmp/s11/$src" "$dest/$dst"
done <<'EOF'
SECTION_11.md                                      SECTION_11.md
DOSSIER_TEMPLATE.md                                DOSSIER_TEMPLATE.md
LICENSE                                            LICENSE.md
examples/reports/BLOCK_REPORT_TEMPLATE.md          reports/BLOCK_REPORT_TEMPLATE.md
examples/reports/POST_WORKOUT_REPORT_TEMPLATE.md   reports/POST_WORKOUT_REPORT_TEMPLATE.md
examples/reports/PRE_WORKOUT_REPORT_TEMPLATE.md    reports/PRE_WORKOUT_REPORT_TEMPLATE.md
examples/reports/WEEKLY_REPORT_TEMPLATE.md         reports/WEEKLY_REPORT_TEMPLATE.md
examples/workout-library/README.md                 workout-library/README.md
examples/workout-library/WORKOUT_REFERENCE.md      workout-library/WORKOUT_REFERENCE.md
EOF

# Rewrite the pin (two lines in README.md).
perl -0pi -e "s/Pinned at commit \`[0-9a-f]+\` \\([0-9-]+\\),\\nprotocol version \\*\\*[0-9.]+\\*\\*/Pinned at commit \`$sha\` ($date),\\nprotocol version **$version**/" "$dest/README.md"
grep -q "$sha" "$dest/README.md" || { echo "pin line in README.md not rewritten -- fix it by hand" >&2; exit 1; }

# Headings SKILL.md tells the coach to grep for. A missing one breaks a job.
missing=0
while IFS= read -r h; do
  grep -qE "^#+ .*$(printf '%s' "$h" | sed 's/[][\.*^$()+?{}|]/\\&/g')" "$dest/SECTION_11.md" \
    || { echo "MISSING heading: $h" >&2; missing=1; }
done <<'EOF'
Behavioral & Analytical Rules for AI Coaches
Readiness Decision
Recovery Metrics Integration
TSB Interpretation
Health Context
Interval Contextualization Rule
Plan Adherence Monitoring
Regression Rule
Zone Distribution & Polarisation Metrics
Phase Detection Criteria
Progression Pathways
AI Training Plan Protocol
Success & Progression Triggers
Rolling Phase Logic
Testing Protocol
Body Weight Handling
Environmental Conditions Protocol
Route & Terrain Protocol
Race-Week Protocol
EOF

echo "section-11 -> $sha ($date), protocol $version"
git -C "$root" diff --stat -- "$dest"
exit "$missing"

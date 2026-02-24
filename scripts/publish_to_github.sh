#!/usr/bin/env bash
set -euo pipefail

# Non-interactive publisher for this repo.
# Required env vars:
# - GITHUB_TOKEN   (repo scope)
# - GITHUB_OWNER   (example: t1m0m)
# - GITHUB_REPO    (example: GMv3-proprietary-universal)
#
# Optional:
# - GITHUB_PRIVATE=true|false (default: true)

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN is required" >&2
  exit 1
fi
if [[ -z "${GITHUB_OWNER:-}" ]]; then
  echo "GITHUB_OWNER is required" >&2
  exit 1
fi
if [[ -z "${GITHUB_REPO:-}" ]]; then
  echo "GITHUB_REPO is required" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VIS="${GITHUB_PRIVATE:-true}"
if [[ "$VIS" != "true" && "$VIS" != "false" ]]; then
  echo "GITHUB_PRIVATE must be true or false" >&2
  exit 1
fi

echo "[publish] ensuring branch is main"
git branch -M main

echo "[publish] creating repo if needed: ${GITHUB_OWNER}/${GITHUB_REPO}"
CREATE_BODY=$(cat <<JSON
{"name":"${GITHUB_REPO}","private":${VIS},"has_issues":true,"has_projects":true,"has_wiki":false}
JSON
)

CREATE_RESP="$(mktemp)"
HTTP_CODE=$(
  curl -sS -o "$CREATE_RESP" -w "%{http_code}" \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/user/repos" \
    -d "$CREATE_BODY"
)

if [[ "$HTTP_CODE" == "201" ]]; then
  echo "[publish] repo created"
elif [[ "$HTTP_CODE" == "422" ]]; then
  echo "[publish] repo already exists (continuing)"
else
  echo "[publish] failed to create repo (HTTP $HTTP_CODE):"
  cat "$CREATE_RESP" >&2
  rm -f "$CREATE_RESP"
  exit 1
fi
rm -f "$CREATE_RESP"

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}.git"
else
  git remote add origin "https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}.git"
fi

PUSH_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_OWNER}/${GITHUB_REPO}.git"

echo "[publish] pushing main"
git push "$PUSH_URL" main:main --set-upstream

echo "[publish] done: https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}"

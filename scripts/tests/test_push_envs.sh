#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

cat >"$tmp_dir/ssh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" >"$SSH_ARGS_CAPTURE"
cat >"$SSH_STDIN_CAPTURE"
SH
chmod +x "$tmp_dir/ssh"

cat >"$tmp_dir/invalid.env" <<'EOF'
POSTGRES_PASSWORD=secret
NEWSLY_DATABASE_URL=postgresql://example
EOF
if PATH="$tmp_dir:$PATH" SSH_ARGS_CAPTURE="$tmp_dir/args" SSH_STDIN_CAPTURE="$tmp_dir/stdin" \
  "$repo_root/scripts/deploy/push_envs.sh" --host example.test --source "$tmp_dir/invalid.env"; then
  echo "missing required key was accepted" >&2
  exit 1
fi
[[ ! -e "$tmp_dir/args" ]]

cat >"$tmp_dir/empty.env" <<'EOF'
POSTGRES_PASSWORD=secret
NEWSLY_DATABASE_URL=
DOCUMENT_EXTRACTOR_SHARED_SECRET=shared
EOF
if PATH="$tmp_dir:$PATH" SSH_ARGS_CAPTURE="$tmp_dir/args" SSH_STDIN_CAPTURE="$tmp_dir/stdin" \
  "$repo_root/scripts/deploy/push_envs.sh" --host example.test --source "$tmp_dir/empty.env"; then
  echo "empty required key was accepted" >&2
  exit 1
fi
[[ ! -e "$tmp_dir/args" ]]

cat >"$tmp_dir/valid.env" <<'EOF'
POSTGRES_PASSWORD=secret
NEWSLY_DATABASE_URL=postgresql://example
DOCUMENT_EXTRACTOR_SHARED_SECRET=shared
EOF
PATH="$tmp_dir:$PATH" SSH_ARGS_CAPTURE="$tmp_dir/args" SSH_STDIN_CAPTURE="$tmp_dir/stdin" \
  "$repo_root/scripts/deploy/push_envs.sh" --host example.test --source "$tmp_dir/valid.env"

cmp -s "$tmp_dir/valid.env" "$tmp_dir/stdin"
grep -Fq "mktemp '/opt/news_app/.env.racknerd.XXXXXX'" "$tmp_dir/args"
grep -Fq 'install -m 600' "$tmp_dir/args"
grep -Fq 'mv -f' "$tmp_dir/args"
grep -Fq 'trap' "$tmp_dir/args"
if grep -Fq '/tmp/' "$tmp_dir/args"; then
  echo "remote command uses a /tmp secret copy" >&2
  exit 1
fi
if grep -Fq "'/opt/news_app/.env'" "$tmp_dir/args"; then
  echo "remote command still writes the unused .env mirror" >&2
  exit 1
fi

echo "Env push contract OK."

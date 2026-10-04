#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

cat >"${tmp_dir}/manifest.json" <<'JSON'
{
  "ok": true,
  "data": {
    "maestro_env": {
      "APP_ID": "org.willemaw.newsly",
      "ASSISTANT_REPLY": "Answer",
      "CHAT_SESSION_ID": "31",
      "SERVER_HOST": "127.0.0.1",
      "SERVER_PORT": "8000",
      "TRANSCRIPT": "Question",
      "USER_ID": "7"
    }
  }
}
JSON
cat >"${tmp_dir}/maestro" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" >"${MAESTRO_CAPTURE}"
SH
chmod +x "${tmp_dir}/maestro"

PATH="${tmp_dir}:${PATH}" MAESTRO_CAPTURE="${tmp_dir}/args" \
  "${repo_root}/scripts/run_maestro_flow.sh" \
  --flow chat_mic_toggle.yaml \
  --record-screenshots \
  --manifest "${tmp_dir}/manifest.json"

grep -Fxq 'test' "${tmp_dir}/args"
grep -Fxq 'APP_ID=org.willemaw.newsly' "${tmp_dir}/args"
grep -Fxq 'CHAT_SESSION_ID=31' "${tmp_dir}/args"
grep -Fxq 'SERVER_HOST=127.0.0.1' "${tmp_dir}/args"
grep -Fxq 'SERVER_PORT=8000' "${tmp_dir}/args"
grep -Fxq 'RECORD_SCREENSHOTS=true' "${tmp_dir}/args"
grep -Fxq 'USER_ID=7' "${tmp_dir}/args"
grep -Fxq "${repo_root}/client/newsly/Maestro/flows/chat_mic_toggle.yaml" "${tmp_dir}/args"

PATH="${tmp_dir}:${PATH}" MAESTRO_CAPTURE="${tmp_dir}/args" \
  "${repo_root}/scripts/run_maestro_flow.sh" \
  --flow chat_mic_toggle.yaml \
  --manifest "${tmp_dir}/manifest.json"
grep -Fxq 'RECORD_SCREENSHOTS=false' "${tmp_dir}/args"

echo "Maestro runner contract OK."

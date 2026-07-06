#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.maestro/bin:$PATH"

if ! java -version >/dev/null 2>&1; then
  for java_bin_dir in /opt/homebrew/opt/openjdk@21/bin /usr/local/opt/openjdk@21/bin; do
    if [[ -x "$java_bin_dir/java" ]]; then
      export PATH="$java_bin_dir:$PATH"
      break
    fi
  done
fi

if ! java -version >/dev/null 2>&1; then
  brew install openjdk@21
  export PATH="$(brew --prefix openjdk@21)/bin:$PATH"
fi

if command -v maestro >/dev/null 2>&1; then
  maestro --version
  exit 0
fi

curl -Ls "https://get.maestro.mobile.dev" | bash
export PATH="$HOME/.maestro/bin:$PATH"
maestro --version

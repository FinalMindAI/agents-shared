#!/usr/bin/env zsh
set -uo pipefail

ROOT="${0:A:h}"
source "$ROOT/threads.zsh"
set -euo pipefail

failures=0
passed=0

assert_eq() {
  local actual="$1"
  local expected="$2"
  local label="$3"
  if [[ "$actual" == "$expected" ]]; then
    passed=$((passed + 1))
    echo "  PASS  $label"
  else
    failures=$((failures + 1))
    echo "  FAIL  $label"
    echo "        expected: $expected"
    echo "        actual:   $actual"
  fi
}

join_args() {
  local IFS=' '
  print -r -- "$*"
}

echo "=== _ai_resume_py_args ==="

_ai_resume_py_args 'ship-pr'
assert_eq "$(join_args "${reply[@]}")" "--find ship-pr" "query only"

_ai_resume_py_args cdx bbbb
assert_eq "$(join_args "${reply[@]}")" "--agent cdx --find bbbb" "cdx scopes lookup"

_ai_resume_py_args cc aaaa-aaaa
assert_eq "$(join_args "${reply[@]}")" "--agent cc --find aaaa-aaaa" "cc scopes lookup"

_ai_resume_py_args grok design-review
assert_eq "$(join_args "${reply[@]}")" "--agent grok --find design-review" "grok scopes lookup"

_ai_resume_py_args cc
assert_eq "$(join_args "${reply[@]}")" "--find cc" "bare agent alias is a query"

if _ai_resume_py_args; then
  failures=$((failures + 1))
  echo "  FAIL  empty args should fail"
else
  passed=$((passed + 1))
  echo "  PASS  empty args should fail"
fi

echo
echo "=== $passed passed, $failures failed ==="
if (( failures > 0 )); then
  exit 1
fi

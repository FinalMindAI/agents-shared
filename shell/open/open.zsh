# `open` shim: a bare number opens that PR in the browser via gh.
# version: 1.0.0
#
#   open 1156      -> gh pr view 1156 --web
#   open .         -> /usr/bin/open .        (real open, unchanged)
#   open -a Safari -> /usr/bin/open -a Safari (real open, unchanged)
#
# Only a single all-digits argument is intercepted; everything else falls
# through to the system `open` via `command open`. To force the real open
# on a file literally named after digits: `command open 123`.
OPEN_ZSH_VERSION="$(sed -n 's/^# version:[[:space:]]*//p' "${${(%):-%x}:A}" | head -1)"

open() {
  if [[ $# -eq 1 && "$1" == --version ]]; then
    # The real `open` has no --version flag, so this is safe to claim.
    print -- "open $OPEN_ZSH_VERSION"
  elif [[ $# -eq 1 && "$1" == <-> ]]; then
    if command -v gh >/dev/null 2>&1; then
      gh pr view "$1" --web
    else
      print -u2 "open: gh not found; cannot open PR #$1"
      return 1
    fi
  else
    command open "$@"
  fi
}

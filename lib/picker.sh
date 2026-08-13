# lib/picker.sh — interactive ANSI multi-select. Sourced by ./install.
# shellcheck shell=bash

# ── interactive multi-select ──────────────────────────────────────────
#
# Usage: multiselect result_var "label1|label2|label3" "desc1|desc2|desc3" "tag1|tag2|tag3" "0|1|0"
#   - result_var: name of variable to store pipe-separated "0|1|0" result
#   - labels:     pipe-separated display names
#   - descs:      pipe-separated descriptions (can be empty strings)
#   - tags:       pipe-separated right-side tags (e.g. "skill", "plugin")
#   - preselect:  pipe-separated 0/1 for initial selection state

multiselect() {
  local result_var="$1"
  IFS='|' read -ra _ms_labels <<< "$2"
  IFS='|' read -ra _ms_descs <<< "$3"
  IFS='|' read -ra _ms_tags <<< "$4"
  IFS='|' read -ra _ms_selected <<< "$5"
  local count=${#_ms_labels[@]}
  local cursor=0

  # Ensure terminal is available
  if [ ! -t 0 ]; then
    # Non-interactive: fall back to select all
    eval "$result_var='$5'"
    return
  fi

  # Save terminal state & hide cursor
  local old_stty
  old_stty="$(stty -g < /dev/tty)"
  stty -icanon -echo min 1 < /dev/tty
  printf "${HIDE_CURSOR}" > /dev/tty

  # Cleanup on exit
  cleanup_multiselect() {
    stty "$old_stty" < /dev/tty
    printf "${SHOW_CURSOR}" > /dev/tty
  }
  trap cleanup_multiselect EXIT

  # Count how many lines we draw so we can clear them
  local total_lines=0

  draw_menu() {
    # Move cursor up to clear previous render (skip on first draw)
    if [ "$total_lines" -gt 0 ]; then
      for ((i = 0; i < total_lines; i++)); do
        printf '\033[A\033[2K' > /dev/tty
      done
    fi
    total_lines=0

    for ((i = 0; i < count; i++)); do
      local prefix="  "
      local check=" "
      local name="${_ms_labels[$i]}"
      local desc="${_ms_descs[$i]:-}"
      local tag="${_ms_tags[$i]:-}"

      if [ "${_ms_selected[$i]}" = "1" ]; then
        check="${GREEN}●${RESET}"
      else
        check="${DIM}○${RESET}"
      fi

      if [ "$i" -eq "$cursor" ]; then
        prefix="${CYAN}❯${RESET}"
      fi

      local tag_str=""
      if [ -n "$tag" ]; then
        tag_str="  ${DIM}${tag}${RESET}"
      fi

      printf "  %b %b  %b%b\n" "$prefix" "$check" "${BOLD}${name}${RESET}" "$tag_str" > /dev/tty
      total_lines=$((total_lines + 1))

      if [ -n "$desc" ]; then
        printf "        ${DIM}%s${RESET}\n" "$desc" > /dev/tty
        total_lines=$((total_lines + 1))
      fi
    done

    printf "\n" > /dev/tty
    total_lines=$((total_lines + 1))

    # Selected count
    local sel_count=0
    for ((i = 0; i < count; i++)); do
      [ "${_ms_selected[$i]}" = "1" ] && sel_count=$((sel_count + 1))
    done

    printf "  ${DIM}↑/↓ navigate  ·  space select  ·  a toggle all  ·  enter confirm  ·  q cancel${RESET}\n" > /dev/tty
    total_lines=$((total_lines + 1))
    printf "  ${BOLD}%d of %d selected${RESET}\n" "$sel_count" "$count" > /dev/tty
    total_lines=$((total_lines + 1))
  }

  draw_menu

  local ESC
  ESC=$(printf '\033')

  # Helper: read one byte from /dev/tty via dd
  read_byte() {
    dd bs=1 count=1 2>/dev/null < /dev/tty
  }

  # Helper: read one byte with short timeout (for escape sequences)
  # Uses stty VMIN=0 VTIME=1 (0.1s timeout)
  read_byte_timeout() {
    stty -icanon min 0 time 1 < /dev/tty
    local byte
    byte="$(dd bs=1 count=1 2>/dev/null < /dev/tty)"
    stty -icanon min 1 time 0 < /dev/tty
    printf '%s' "$byte"
  }

  while true; do
    # Read a single character from /dev/tty
    local char
    char="$(read_byte)"

    if [ "$char" = "$ESC" ]; then
      # Read escape sequence with timeout so bare ESC doesn't hang
      local seq1="" seq2=""
      seq1="$(read_byte_timeout)"
      seq2="$(read_byte_timeout)"
      if [ "$seq1" = "[" ]; then
        case "$seq2" in
          A) # Up
            if [ "$cursor" -gt 0 ]; then
              cursor=$((cursor - 1))
            else
              cursor=$((count - 1))
            fi
            ;;
          B) # Down
            if [ "$cursor" -lt $((count - 1)) ]; then
              cursor=$((cursor + 1))
            else
              cursor=0
            fi
            ;;
        esac
      fi
    elif [ "$char" = " " ]; then
      # Space — toggle current
      if [ "${_ms_selected[$cursor]}" = "1" ]; then
        _ms_selected[$cursor]=0
      else
        _ms_selected[$cursor]=1
      fi
    elif [ "$char" = "a" ] || [ "$char" = "A" ]; then
      # Toggle all
      local all_selected=1
      for ((i = 0; i < count; i++)); do
        [ "${_ms_selected[$i]}" = "0" ] && all_selected=0 && break
      done
      local new_val=1
      [ "$all_selected" -eq 1 ] && new_val=0
      for ((i = 0; i < count; i++)); do
        _ms_selected[$i]=$new_val
      done
    elif [ "$char" = "" ]; then
      # Enter — confirm
      break
    elif [ "$char" = "q" ] || [ "$char" = "Q" ]; then
      # Cancel
      for ((i = 0; i < count; i++)); do
        _ms_selected[$i]=0
      done
      _ms_selected[0]="cancel"
      break
    elif [ "$char" = "j" ]; then
      if [ "$cursor" -lt $((count - 1)) ]; then
        cursor=$((cursor + 1))
      else
        cursor=0
      fi
    elif [ "$char" = "k" ]; then
      if [ "$cursor" -gt 0 ]; then
        cursor=$((cursor - 1))
      else
        cursor=$((count - 1))
      fi
    fi

    draw_menu
  done

  # Restore terminal
  trap - EXIT
  cleanup_multiselect

  # Build result
  local result=""
  for ((i = 0; i < count; i++)); do
    [ -n "$result" ] && result="$result|"
    result="$result${_ms_selected[$i]}"
  done
  eval "$result_var='$result'"
}


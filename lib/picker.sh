# lib/picker.sh — interactive ANSI multi-select. Sourced by ./install.
# shellcheck shell=bash

# ── interactive multi-select ──────────────────────────────────────────
#
# Usage: multiselect result_var "label1|label2" "desc1|desc2" "tag1|tag2" "0|1" "skill|plugin"
#   - result_var: name of variable to store pipe-separated "0|1|0" result
#   - labels:     pipe-separated display names
#   - descs:      pipe-separated descriptions (can be empty strings)
#   - tags:       pipe-separated right-side tags (e.g. "skill", "plugin")
#   - preselect:  pipe-separated 0/1 for initial selection state
#   - types:      pipe-separated item type per row; drives the tab bar
#                 (All + one tab per distinct type). Selection persists across tabs.
#   - title:      heading drawn above the list
#
# Renders in the terminal's alternate screen and repaints it whole on every
# keypress, so wrapped lines never leave residue. Restores the main screen
# (and whatever was there) on exit.

multiselect() {
  local result_var="$1"
  IFS='|' read -ra _ms_labels <<< "$2"
  IFS='|' read -ra _ms_descs <<< "$3"
  IFS='|' read -ra _ms_tags <<< "$4"
  IFS='|' read -ra _ms_selected <<< "$5"
  IFS='|' read -ra _ms_types <<< "${6:-}"
  local title="${7:-}"
  local count=${#_ms_labels[@]}
  local cursor=0

  # Tabs: "all" + each distinct type in first-seen order
  local tabs=(all) t
  for ((i = 0; i < count; i++)); do
    t="${_ms_types[$i]:-}"
    [ -z "$t" ] && continue
    local seen=0
    for x in "${tabs[@]}"; do [ "$x" = "$t" ] && seen=1; done
    [ "$seen" -eq 0 ] && tabs+=("$t")
  done
  local tab=0
  local tab_count=${#tabs[@]}

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
  printf "${ALT_SCREEN_ON}${HIDE_CURSOR}" > /dev/tty

  # Cleanup on exit
  cleanup_multiselect() {
    stty "$old_stty" < /dev/tty
    printf "${SHOW_CURSOR}${ALT_SCREEN_OFF}" > /dev/tty
  }
  trap cleanup_multiselect EXIT

  # Indices of rows shown on the current tab
  local visible=()
  refresh_visible() {
    visible=()
    for ((i = 0; i < count; i++)); do
      if [ "${tabs[$tab]}" = "all" ] || [ "${_ms_types[$i]:-}" = "${tabs[$tab]}" ]; then
        visible+=("$i")
      fi
    done
    local vcount=${#visible[@]}
    if [ "$vcount" -eq 0 ]; then
      cursor=0
    elif [ "$cursor" -ge "$vcount" ]; then
      cursor=$((vcount - 1))
    fi
  }
  refresh_visible

  draw_menu() {
    printf "${CLEAR_SCREEN}" > /dev/tty
    if [ -n "$title" ]; then
      printf "\n  ${BOLD}%s${RESET}\n\n" "$title" > /dev/tty
    fi

    # Tab bar (only when there is more than one type)
    if [ "$tab_count" -gt 1 ]; then
      local bar="  "
      for ((ti = 0; ti < tab_count; ti++)); do
        local tname="${tabs[$ti]}" n=0
        for ((i = 0; i < count; i++)); do
          if [ "$tname" = "all" ] || [ "${_ms_types[$i]:-}" = "$tname" ]; then n=$((n + 1)); fi
        done
        local label
        case "$tname" in
          all)    label="All" ;;
          shell)  label="Shell helpers" ;;
          *)      label="$(printf '%s' "${tname:0:1}" | tr '[:lower:]' '[:upper:]')${tname:1}s" ;;
        esac
        if [ "$ti" -eq "$tab" ]; then
          bar="$bar${REVERSE}${BOLD} $label ($n) ${RESET}  "
        else
          bar="$bar${DIM} $label ($n) ${RESET}  "
        fi
      done
      printf "%b\n\n" "$bar" > /dev/tty
    fi

    local vcount=${#visible[@]}
    if [ "$vcount" -eq 0 ]; then
      printf "  ${DIM}(nothing in this tab)${RESET}\n" > /dev/tty
    fi

    for ((v = 0; v < vcount; v++)); do
      local i="${visible[$v]}"
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

      if [ "$v" -eq "$cursor" ]; then
        prefix="${CYAN}❯${RESET}"
      fi

      local tag_str=""
      if [ -n "$tag" ]; then
        tag_str="  ${DIM}${tag}${RESET}"
      fi

      printf "  %b %b  %b%b\n" "$prefix" "$check" "${BOLD}${name}${RESET}" "$tag_str" > /dev/tty

      if [ -n "$desc" ]; then
        printf "        ${DIM}%s${RESET}\n" "$desc" > /dev/tty
      fi
    done

    printf "\n" > /dev/tty

    # Selected count (across all tabs)
    local sel_count=0
    for ((i = 0; i < count; i++)); do
      [ "${_ms_selected[$i]}" = "1" ] && sel_count=$((sel_count + 1))
    done

    local help="↑/↓ navigate  ·  space select  ·  a toggle all"
    [ "$tab_count" -gt 1 ] && help="←/→ or tab switch tab  ·  $help in tab"
    printf "  ${DIM}%s  ·  enter confirm  ·  q cancel${RESET}\n" "$help" > /dev/tty
    printf "  ${BOLD}%d of %d selected${RESET}\n" "$sel_count" "$count" > /dev/tty
  }

  draw_menu

  local ESC TAB
  ESC=$(printf '\033')
  TAB=$(printf '\t')

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

  move_up() {
    local vcount=${#visible[@]}
    [ "$vcount" -eq 0 ] && return
    if [ "$cursor" -gt 0 ]; then cursor=$((cursor - 1)); else cursor=$((vcount - 1)); fi
  }
  move_down() {
    local vcount=${#visible[@]}
    [ "$vcount" -eq 0 ] && return
    if [ "$cursor" -lt $((vcount - 1)) ]; then cursor=$((cursor + 1)); else cursor=0; fi
  }
  next_tab() { tab=$(( (tab + 1) % tab_count )); cursor=0; refresh_visible; }
  prev_tab() { tab=$(( (tab + tab_count - 1) % tab_count )); cursor=0; refresh_visible; }

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
          A) move_up ;;
          B) move_down ;;
          C) next_tab ;;
          D) prev_tab ;;
        esac
      fi
    elif [ "$char" = "$TAB" ] || [ "$char" = "l" ]; then
      next_tab
    elif [ "$char" = "h" ]; then
      prev_tab
    elif [ "$char" = " " ]; then
      # Space — toggle current
      if [ ${#visible[@]} -gt 0 ]; then
        local i="${visible[$cursor]}"
        if [ "${_ms_selected[$i]}" = "1" ]; then
          _ms_selected[$i]=0
        else
          _ms_selected[$i]=1
        fi
      fi
    elif [ "$char" = "a" ] || [ "$char" = "A" ]; then
      # Toggle all rows in the current tab
      local all_selected=1
      for i in "${visible[@]+"${visible[@]}"}"; do
        [ "${_ms_selected[$i]}" = "0" ] && all_selected=0 && break
      done
      local new_val=1
      [ "$all_selected" -eq 1 ] && new_val=0
      for i in "${visible[@]+"${visible[@]}"}"; do
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
      move_down
    elif [ "$char" = "k" ]; then
      move_up
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

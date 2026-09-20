# skills — audit where coding-agent skills live and how they are linked.
# version: 1.0.0
#
#   source /path/to/agents-shared/shell/skills/skills.zsh   # in ~/.zshrc
#
#   skills                   # fzf picker: tabs per location, enter=info,
#                            #   ctrl-s=sync, ctrl-l=link, ctrl-o=open
#   skills --json            # machine-readable (what the /audit-skills skill uses)
#   skills info|sync|link <name>
#
# Canonical (synced) dir defaults to ~/Dropbox/dev/agents/skills; override
# with SKILLS_CANONICAL_DIR before sourcing. Exported as $SKILLS_PY so the
# Claude Code /audit-skills skill can find the engine.

export SKILLS_PY="${${(%):-%x}:A:h}/skills.py"
SKILLS_ZSH_VERSION="$(sed -n 's/^# version:[[:space:]]*//p' "${${(%):-%x}:A}" | head -1)"

skills() {
    if [[ "$1" == --version ]]; then
        print -- "skills $SKILLS_ZSH_VERSION"
        return 0
    fi
    python3 "$SKILLS_PY" "$@"
}

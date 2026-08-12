# Claude Code Shared Skills & Plugins

A collection of shared skills, plugins, and notification helpers for Claude Code and Codex CLI.

## Quick start with mise

[mise](https://mise.jdx.dev) is the front door. It pins the one tool the installer needs (`python3`) and wraps the `install` CLI in named tasks:

```bash
mise setup                     # interactive picker — choose what to install into ~/.claude
mise run install:all           # install every first-party skill & plugin, no prompts
mise run install -- notify awake   # install specific items by name
mise run list                  # show what's installed
mise run update                # pull latest for cloned external repos
mise run add -- garrytan/gstack    # clone a GitHub repo and pick items
```

Groups are data-driven — edit [`groups.json`](groups.json) to define named sets of items, then install one with `./install get --group <name>`. Today there's a single `all` group covering everything first-party.

## Installing skills & plugins from GitHub

Run `./install` to pick from everything available and install to `~/.claude/`:

```bash
./install                         # interactive picker for all skills & plugins
./install add garrytan/gstack     # clone a GitHub repo and pick items to add
./install list                    # show what's installed
./install remove <name>           # remove a skill or plugin
./install update                  # pull latest for all repos
```

External repos are cloned to `repos/<user>/<repo>/` (gitignored) and symlinked into `skills/` and `plugins/`. This keeps upstream repos intact for easy `git pull` updates.

## First-party skills

### [convert-video-to-sop](skills/convert-video-to-sop/)

Convert screen-recording videos (single file or directory) into illustrated Standard Operating Procedures, output as Word + PDF. Local preprocessing (ffmpeg scene detection, tesseract OCR, classified frame diffs with adaptive densification) keeps token cost ~90% below naive frame-by-frame analysis; estimates cost and confirms with the user before any AI spend. Requires `ffmpeg`, `tesseract`; `pandoc` + LibreOffice for docx/PDF; `whisper-cpp` optional for narrated videos.

## Plugins

### [notify](plugins/notify/)

macOS notifications with sound, topic summarization, and terminal activation when Claude Code finishes or needs input.

### [awake](plugins/awake/)

Starts an Amphetamine session when the first Claude Code or Codex session begins, and ends it when the last one stops.

### [codex-notify](plugins/codex-notify/)

macOS notifications with sound and terminal activation when Codex CLI finishes a turn.

### [codex-awake](plugins/codex-awake/)

Codex hook wrapper for the shared Amphetamine keep-awake helper.

## Usage

Install a plugin:

```bash
claude plugin add /path/to/claude-code-shared/plugins/<plugin-name>
```

Or test locally:

```bash
claude --plugin-dir /path/to/claude-code-shared/plugins/<plugin-name>
```

For Codex CLI, add the helper script to `~/.codex/config.toml`:

```toml
notify = ["/path/to/claude-code-shared/plugins/codex-notify/notify-sound.sh"]
```

For Codex session start/stop hooks, create `~/.codex/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/claude-code-shared/plugins/codex-awake/awake.sh start"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/claude-code-shared/plugins/codex-awake/awake.sh stop"
          }
        ]
      }
    ]
  }
}
```

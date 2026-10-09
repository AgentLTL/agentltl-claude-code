#!/usr/bin/env bash
# Runs inside the agentltl-claude image (see ../e2e.sh): installs the plugin from /src as a user
# would (a local marketplace), starts the scripted model, runs one `claude -p` session in
# ~/work with AGENTLTL.yaml, and leaves what happened in /out. Claude Code talks to the
# scripted model (ANTHROPIC_BASE_URL): no account is needed.
set -u
here=/src/docker/e2e
export ANTHROPIC_BASE_URL=http://127.0.0.1:8999 ANTHROPIC_API_KEY=x \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1
cp -r /src /tmp/plugin && rm -rf /tmp/plugin/.venv
mkdir -p ~/work && cd ~/work && git init -q && printf 'hello\n' > README.md \
    && git add README.md && git commit -qm init
cp "$here/AGENTLTL.yaml" ~/work/
claude --version > /out/version.txt 2>&1
{ claude plugin marketplace add /tmp/plugin && claude plugin install agentltl@agentltl; } \
    > /out/install.txt 2>&1
python3 /src/vendor/agentltl-coding/e2e/scripted_model.py "$here/script.json" --log /out &
sleep 1
timeout 600 claude -p "do the scripted steps" --dangerously-skip-permissions \
    --output-format json > /out/claude.json 2> /out/claude-stderr.txt
echo "exit $?" >> /out/claude-stderr.txt
"$(dirname "$(find ~/.claude/plugins -path '*agentltl*' -name agentltl -path '*/bin/*' | head -1)")/agentltl" \
    trace > /out/trace.txt 2>&1

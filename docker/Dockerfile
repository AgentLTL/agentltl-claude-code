# Claude Code with Python, for testing the plugin end to end. The Claude Code version is pinned
# in package.json (Dependabot proposes each new release).
#
#   docker/e2e.sh          # a scripted model (ANTHROPIC_BASE_URL), no account needed
FROM node:22-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates jq \
 && rm -rf /var/lib/apt/lists/*
COPY package.json /opt/agentltl/package.json
RUN npm install -g "@anthropic-ai/claude-code@$(jq -r '.dependencies["@anthropic-ai/claude-code"]' /opt/agentltl/package.json)"
# Claude Code refuses --dangerously-skip-permissions as root.
USER node
RUN git config --global user.name tester && git config --global user.email tester@example.com \
 && git config --global init.defaultBranch main
WORKDIR /home/node
CMD ["claude"]

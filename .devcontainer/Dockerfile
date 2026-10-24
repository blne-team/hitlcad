# syntax=docker/dockerfile:1

# One development image, two modeling runtimes:
# - /usr/local/bin/python -> standard Python + build123d + Trimesh
# - /opt/blender/blender -> Blender with bundled Python (bpy)


FROM ghcr.io/astral-sh/uv:python3.11-trixie AS dev

ARG USERNAME=user
ARG USER_UID=1000
ARG USER_GID=1000


ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/blender:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENV HOME=/home/user

ENV VIRTUAL_ENV=/opt/venv

ENV UV_TOOL_BIN_DIR=/home/user/.local/bin

ENV CARGO_HOME=/home/user/.cargo
ENV RUSTUP_HOME=/home/user/.rustup
ENV PATH=$CARGO_HOME/bin:$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR:$PATH
ENV UV_PROJECT_ENVIRONMENT=$VIRTUAL_ENV


ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Blender's pre-built Linux release in this starter targets x86-64.
RUN test "$(dpkg --print-architecture)" = "amd64" || \
    (echo 'This Dockerfile requires linux/amd64; set platform: linux/amd64 in compose.' >&2; exit 1)

RUN --mount=type=cache,target=/var/cache/apt/ \
    --mount=type=cache,target=/var/lib/apt/ \
    apt-get update && \
    apt-get install --no-install-recommends --yes \
      build-essential \
      ca-certificates \
      clang \
      curl \
      git \
      graphviz \
      libssl-dev \
      lld \
      locales \
      pkg-config \
      sqlite3 \
      ssh \
      sudo \
      vim \
      tmux \
      libgl1 libegl1 libopengl0 libglu1-mesa \
      libnspr4 libnss3 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 libcups2t64 \
      libxcomposite1 libxdamage1 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 \
      libx11-6 libx11-xcb1 libxext6 libxrender1 libxi6 \
      libxfixes3 libxxf86vm1 libxkbcommon0 libsm6 libxrandr2 \
      libxcursor1 libxinerama1 libfontconfig1 libfreetype6 \
      libdbus-1-3 libwayland-client0 libglib2.0-0 libgomp1 \
      wget && \
    groupadd --gid $USER_GID user && \
    useradd --create-home --no-log-init --gid $USER_GID --uid $USER_UID --shell /usr/bin/bash user && \
    mkdir -p /opt/venv && chown user:user /opt/venv && \
    echo 'user ALL=(root) NOPASSWD:ALL' > /etc/sudoers.d/user && chmod 0440 /etc/sudoers.d/user


RUN git config --system --add safe.directory '*'
RUN sh -c "$(curl -fsSL https://starship.rs/install.sh)" -- --yes
RUN printf 'export PATH="%s:$PATH"\n' "$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR" > /etc/profile.d/devcontainer-path.sh

SHELL ["/bin/bash", "-c"]

USER user
RUN mkdir -p "$HOME/.config" "$HOME/.local/bin" "$HOME/.local/share" "$CARGO_HOME/registry" "$CARGO_HOME/git" "$RUSTUP_HOME"

# Create the bind mount's parent as user before Docker mounts starship.toml.
# RUN mkdir -p "$HOME/.config" "$HOME/.local/bin" "$HOME/.local/share" "$CARGO_HOME" "$RUSTUP_HOME"

RUN uv venv "${VIRTUAL_ENV}"

ARG RUST_VERSION="stable"

# Separate caches by UID/GID to avoid reusing old root-owned caches.
RUN --mount=type=cache,id=solidwork-cargo-registry-${USER_UID}-${USER_GID},target=/home/user/.cargo/registry,uid=${USER_UID},gid=${USER_GID} \
    --mount=type=cache,id=solidwork-cargo-git-${USER_UID}-${USER_GID},target=/home/user/.cargo/git,uid=${USER_UID},gid=${USER_GID} \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
      --default-toolchain ${RUST_VERSION} \
      --profile default \
      --no-modify-path && \
    cargo --version && \
    rustc --version && \
    rustup component add rust-analyzer clippy rustfmt

ARG NODE_VERSION="24"
ARG NVM_VERSION="0.40.3"
ARG PNPM_VERSION="latest"
ENV NVM_DIR=/home/user/.nvm
ENV NVM_SYMLINK_CURRENT=true
ENV PATH="${NVM_DIR}/current/bin:${PATH}"
RUN mkdir -p "$NVM_DIR" \
    && curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh | bash \
    && source "$NVM_DIR/nvm.sh" \
    && nvm install "$NODE_VERSION" \
    && nvm alias default "$NODE_VERSION" \
    && nvm use default \
    && corepack enable \
    && corepack prepare pnpm@${PNPM_VERSION} --activate

ARG BLENDER_VERSION="4.5.14"
USER root
RUN curl --fail --location --show-error --silent \
    "https://download.blender.org/release/Blender4.5/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
    -o /tmp/blender.tar.xz && \
    mkdir -p /opt/blender && \
    tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 --no-same-owner && \
    chmod -R a+rX /opt/blender && \
    rm -f /tmp/blender.tar.xz
USER user


# -------------------------------------------------------------------
# 4. Update Shell Profile / PATH configuration for interactive shells
# -------------------------------------------------------------------
RUN mkdir -p "$HOME/.history" && \
    echo 'export HISTFILE=$HOME/.history/.bash_history' >> "$HOME/.bashrc" && \
    echo 'export HISTSIZE=10000'                        >> "$HOME/.bashrc" && \
    echo 'export HISTFILESIZE=20000'                    >> "$HOME/.bashrc" && \
    echo 'export HISTCONTROL=ignoredups:erasedups'      >> "$HOME/.bashrc" && \
    echo 'shopt -s histappend'                          >> "$HOME/.bashrc" && \
    echo 'PROMPT_COMMAND="history -a; history -c; history -r"' >> "$HOME/.bashrc" && \    
    echo '[[ $- == *i* ]] && bind "\e[A":history-search-backward' >> "$HOME/.bashrc" && \
    echo '[[ $- == *i* ]] && bind "\e[B":history-search-forward'  >> "$HOME/.bashrc" && \
    echo 'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi'  > "$HOME/.bash_profile" && \
    echo 'export NVM_DIR=/home/user/.nvm'                          >> "$HOME/.bashrc" && \
    echo 'export CARGO_HOME=/home/user/.cargo'                    >> "$HOME/.bashrc" && \
    echo 'export RUSTUP_HOME=/home/user/.rustup'                  >> "$HOME/.bashrc" && \
    echo 'export PATH=$CARGO_HOME/bin:$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR:$NVM_DIR/current/bin:$PATH' >> "$HOME/.bashrc" && \
    echo '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'          >> "$HOME/.bashrc" && \
    echo '[ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"' >> "$HOME/.bashrc"

SHELL ["/bin/bash", "-lc"]

USER root
RUN mkdir -p /workspaces/solidwork && chown user:user /workspaces/solidwork
USER user

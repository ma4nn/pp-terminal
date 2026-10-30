FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends git patch \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY . .

# hatch-vcs reads the version from git. VERSION overrides that when the
# build context has no history (a PEP 440 version, e.g. 0.12.1).
ARG VERSION

RUN git config --global --add safe.directory /app \
    && if [ ! -f ppxml2db/ppxml2db.py ]; then \
         echo "ppxml2db submodule is missing; run: git submodule update --init" >&2; \
         exit 1; \
       fi \
    && if patch -p1 -R --dry-run --silent < patch_ppxml2db.diff >/dev/null 2>&1; then \
         echo "ppxml2db patch already applied"; \
       else \
         patch -p1 --forward --batch < patch_ppxml2db.diff; \
       fi \
    && if [ -n "${VERSION}" ]; then \
         export SETUPTOOLS_SCM_PRETEND_VERSION="${VERSION}"; \
       elif ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
         echo "No git history in the build context. Rebuild with --build-arg VERSION=<pep440 version>." >&2; \
         exit 1; \
       fi \
    && uv sync --frozen --no-dev --no-install-project --no-build --python /usr/local/bin/python3 \
    && uv build --wheel --out-dir /tmp/dist \
    && uv pip install --no-build --no-deps --python /app/.venv/bin/python /tmp/dist/*.whl

FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends locales \
    && sed -i 's/^# *\(de_DE.UTF-8 UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 pp-terminal \
    && mkdir -p /data \
    && chown pp-terminal:pp-terminal /data

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH="/app/.venv/bin:$PATH" \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv

USER pp-terminal

WORKDIR /data

ENTRYPOINT ["pp-terminal"]
CMD ["--help"]

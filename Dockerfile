# CFO Office / O2C control tower - container image.
#
# The image has NO .git directory (excluded via .dockerignore) and no git binary,
# so o2c_orchestrator._git_commit_hash() cannot shell out to git at runtime. The
# build stamps the commit identity into the image instead, via the GIT_COMMIT
# build arg -> env, and the orchestrator reads GIT_COMMIT before trying git.
#
# Build (stamp the image with the current commit):
#   docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) -t cfo-office:dev .
#
# Run (persist the per-run output history to a host volume):
#   docker run --rm -v "$(pwd)/o2c-outputs:/app/cfo-office/o2c/outputs" cfo-office:dev
#
FROM python:3.12-slim

WORKDIR /app

# Dependencies match CI (.github/workflows/ci.yml).
RUN pip install --no-cache-dir requests anthropic python-dotenv mcp pandas

COPY . .

# Commit identity comes from the build, not from git at runtime (the image has no
# .git and no git binary). o2c_orchestrator._git_commit_hash() reads GIT_COMMIT
# first, then falls back to git. Pass the real commit at build time via
# --build-arg (see the build command above); left as "unknown" if not supplied.
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT}

# Default: run the O2C control tower comparison, archiving per-run history under
# the mounted outputs volume (cfo-office/o2c/outputs/runs/<run_id>/<period>/).
CMD ["python", "run_o2c_control_tower.py", "--compare"]

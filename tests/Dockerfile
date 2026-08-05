# Test image — built FROM the already-built production image, never part
# of the default `docker build .` (which only ever produces production).
#
# Build:  docker build -t engram .
#         docker build -f tests/Dockerfile -t engram-test .
# Run:    docker run --rm engram-test

FROM engram:latest

USER root

# pyproject.toml/uv.lock (with the pinned "test" group) were left in /app by
# the production build — export just that group and install it straight
# into the system interpreter, same as the production layer does.
RUN uv export --locked --only-group test --no-emit-project --no-hashes -o /tmp/requirements-test.txt \
    && uv pip install --system --no-cache -r /tmp/requirements-test.txt \
    && rm /tmp/requirements-test.txt

# tests/ expects a sibling src/ dir (Path(__file__).parent.parent / "src");
# the production image flattens src/ straight into /app, so point a symlink
# at it instead of restructuring the production image layout.
RUN ln -s /app /app/src
COPY tests/ ./tests/
RUN chown engram:engram /app /app/tests

USER engram

ENTRYPOINT ["python", "-m", "pytest"]
CMD ["tests/", "-v"]

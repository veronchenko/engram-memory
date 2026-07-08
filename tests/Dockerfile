# Test image — built FROM the already-built production image, never part
# of the default `docker build .` (which only ever produces production).
#
# Build:  docker build -t engram .
#         docker build -f tests/Dockerfile -t engram-test .
# Run:    docker run --rm engram-test

FROM engram:latest

USER root

RUN pip install --no-cache-dir pytest==8.4.2 httpx==0.28.1

# tests/ expects a sibling src/ dir (Path(__file__).parent.parent / "src");
# the production image flattens src/ straight into /app, so point a symlink
# at it instead of restructuring the production image layout.
RUN ln -s /app /app/src
COPY tests/ ./tests/
RUN chown engram:engram /app /app/tests

USER engram

ENTRYPOINT ["python", "-m", "pytest"]
CMD ["tests/", "-v"]

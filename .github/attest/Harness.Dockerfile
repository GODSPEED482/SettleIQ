# The test harness for one certification run (spec 9.7.2). It starts FROM the subject image by
# digest and adds only test code and test dependencies; check_layers.py proves the subject's
# layers are unchanged underneath.
ARG SUBJECT
FROM ${SUBJECT}
COPY backend/tests /srv/app/tests
RUN uv sync --frozen --no-install-project --only-group dev --inexact

# Headless Pd for untrusted patches. See docs/SANDBOX.md.
#   docker build -t pd-sandbox sandbox/
FROM debian:bookworm-slim@sha256:e67eeead736ff7bbe258ef088bff683635c561b1fcfefad0806c1df4ab49ce0e AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates gcc git libc6-dev make
# libpd 0.16.1 (Pd 0.56), pinned by commit so a moved tag cannot change it.
ARG LIBPD_COMMIT=ba0dc63262901d658af8bbda5e619a60fa975e78
RUN git init -q /libpd && cd /libpd \
    && git fetch -q --depth 1 https://github.com/libpd/libpd.git "$LIBPD_COMMIT" \
    && git checkout -q FETCH_HEAD \
    && git submodule update -q --init --depth 1 pure-data
# Not the default -ffast-math: NaN and infinity must behave as they do in Pd.
RUN make -C /libpd -j"$(nproc)" EXTRA=true STATIC=true \
        OPT_CFLAGS="-O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2" libs/libpd.a
COPY pdrun.c /src/
RUN gcc -O2 -Wall -Wextra -fstack-protector-strong -fPIE -pie -Wl,-z,relro,-z,now \
        -I/libpd/libpd_wrapper -I/libpd/pure-data/src \
        -o /pdrun /src/pdrun.c /libpd/libs/libpd.a -lm -lpthread -ldl

# Distroless: no shell, package manager or interpreter for a patch to use.
FROM gcr.io/distroless/cc-debian12:nonroot@sha256:9dac0a79194e45a7da0158a9c6da57b217585af0786db3845d1f0ec1a0dd182f
COPY --from=build /pdrun /pdrun
WORKDIR /work
USER 65532:65532
ENTRYPOINT ["/pdrun"]

# Headless Pd sandbox

`sandbox/` runs an untrusted `.pd` file with no GUI and returns its console
output, a WAV file and an exit status. It is how the agent checks its own patches
(`agent/tools/patch.py`).

```bash
docker build -t pd-sandbox sandbox/
```

```bash
python -m sandbox patch.pd --seconds 3 --out out.wav
```

From Python (blocking, so call it from a worker thread):

```python
from sandbox import run_patch
result = run_patch(pd_text, seconds=3)
result.status, result.exit_code, result.console, result.wav
```

## How it works

1. `runner.py` starts one new container (or Modal VM) per run, with the patch on stdin.
2. `pdrun.c` forks a child, which loads the patch into Pd through
   [libpd](https://github.com/libpd/libpd) and renders `[dac~]` offline, usually
   much faster than real time. Both channels are recorded at 48 kHz.
3. The child's audio goes to shared memory and its console to a pipe. The
   parent enforces the time limit, works out how the child ended, and writes one
   framed result to stdout.
4. `runner.py` validates the frame and builds the WAV.

Pd is 0.56, the vanilla objects plus libpd's bundled extras. libpd is used
because stock Pd has no way to record whatever a patch sends to `[dac~]`
without editing the patch or having a sound card. Two differences from desktop
Pd follow: some error lines carry a `verbose(n):` prefix, and `; pd exit` is
ignored (`; pd quit` does quit).

| Status | Meaning |
|---|---|
| `ok` | Rendered the full duration. The console may still contain Pd errors. |
| `timeout` | Hit the wall-clock limit. The WAV holds the audio rendered so far. |
| `memory_limit` | Killed by the kernel for using too much memory. |
| `file_size_limit` | Killed for writing past the file size limit. |
| `crashed` | Pd died from another signal, for example a segfault. |
| `load_failed` | Pd could not open the file as a patch. |
| `patch_exited` | The patch quit Pd early. |

A `SandboxError` means the sandbox itself failed and says nothing about the patch.

## Hardening choices

This section describes the Docker backend. Differences on Modal are in [their own section](#running-on-modal).

The threat is a patch that is malicious or just wrong: endless loops, huge
arrays, file writes, network objects, or a memory-safety bug in Pd (it is C, and
its file parser now reads model-written text).

| Choice | Setting | Why |
|---|---|---|
| No network | `--network=none` | `[netsend]`, `[netreceive]` and any exploit have nowhere to connect. |
| CPU | `--cpus=1` | One run cannot starve the host. |
| Memory | `--memory=256m`, swap equal | The kernel kills the run, not the host. The child raises its own OOM score, so the supervisor survives to report it. |
| Wall clock | 15 s by default, 60 s at most | Enforced by the supervisor. An alarm in the supervisor and a host-side deadline back it up. |
| Processes | `--pids-limit=32` | Stops fork and thread bombs. |
| Read-only base image | `--read-only` | Nothing outside `/work` can be written. `--ipc=none` removes `/dev/shm`. |
| Temporary filesystem | 32 MB tmpfs at `/work`, `noexec,nosuid,nodev` | Holds the patch and its files, and disappears with the container. |
| File size | `--ulimit fsize` 16 MB | A single file cannot fill the tmpfs. Also no core dumps, 64 open files. |
| Output size | Console 64 KB, audio 30 s (5.8 MB), patch 1 MB | Capped in the container, then again on the host, which does not trust the container's lengths. |
| No host mounts | Patch on stdin, result on stdout | There is no host path to escape through, and no Docker socket. |
| No host logs | `--log-driver=none` | A flood of output cannot fill the host's disk. |
| Privileges | uid 65532, `--cap-drop=ALL`, `no-new-privileges`, Docker's default seccomp profile | Limits what an exploited Pd can ask of the kernel. |
| Minimal image | Distroless: no shell, package manager or interpreter | Little for an exploit to use. |
| Pinned build | Base images by digest, libpd by commit, `--pull=never` at run time | The image cannot change underneath us. |
| Syscall denylist | A seccomp filter on the patch process | Blocks starting programs, mounts, ptrace, BPF, io_uring and other calls Pd never needs. |
| Supervisor split | Child has no access to the real stdout; the parent is not dumpable | A patch cannot forge its result by printing. |
| Cleanup | `--rm`, and `docker rm -f` on any host-side failure | No container outlives its run, even if the host process is killed. |
| Concurrency | 2 runs at once (`PD_SANDBOX_CONCURRENCY`), across the whole deployment when it has a Postgres database | Bounds the load, and the Modal spend, however many workers and replicas serve requests. See [Concurrency](#concurrency). |

`tests/test_sandbox.py` checks these limits from inside a running container, as
well as runaway patches. It is skipped when the image is not built;
`.github/workflows/sandbox.yml` builds it and requires it.

## Limits of this design

- **Shared kernel (Docker only).** With the default `runc` runtime, a Linux kernel exploit
  could escape. Set `PD_SANDBOX_RUNTIME=runsc` to run under
  [gVisor](https://gvisor.dev) instead. That path has not been tested yet. In
  particular, gVisor may kill the whole container on running out of memory,
  which would show up as a `SandboxError` rather than `memory_limit`.
- **Docker access (Docker only) is root-equivalent.** Whatever calls `run_patch` needs the
  Docker socket. Run it in a small worker rather than in the public API process.
- **Production cannot use Docker on Railway**, which has no Docker daemon. Use Modal there.
- A patch can read the image's own files and its `/proc`. Neither holds secrets:
  no environment variables are passed in.


## Concurrency

`PD_SANDBOX_CONCURRENCY` caps how many patches run at once
([`sandbox/slots.py`](../sandbox/slots.py)). Each run first takes a slot in its
own process, so one process never goes past the cap. When `PD_SANDBOX_LOCK_URL`
(or, if it is unset, `DATABASE_URL`) is a Postgres URL, the run also takes one of
`PD_SANDBOX_CONCURRENCY` Postgres session advisory locks, keyed
`(0x50645362, slot)`. Every worker process and replica that shares the database
shares those locks, so the cap holds for the whole deployment and Modal spend
does not grow with the number of workers. A lock belongs to its database
connection, so a process killed mid-run frees its slot when Postgres drops the
connection. A run that cannot get a slot within 60 seconds fails with "sandbox is
busy", which the agent reports as the sandbox being unavailable.

Without a Postgres URL (local work, SQLite), the cap is per process, so the total
is the number of processes times the setting. If the lock database cannot be
reached, runs go ahead under the per-process cap and a warning is logged, rather
than every patch run failing during a database outage.

Production runs one uvicorn worker on one Railway replica with `DATABASE_URL`
set to its Postgres, so the cap is deployment-wide already and stays so if
workers or replicas are added. `tests/test_sandbox_slots.py` checks the slot
logic, and with `TEST_DATABASE_URL` set it checks that two sessions contend for
one slot and that a killed process frees its slot.

## Running on Modal

**Decision (TASK-5.26, 2026-11-24): go.** Modal hosts the sandbox in production;
Docker stays for local development and CI. `PD_SANDBOX_BACKEND=modal` selects it.

It uses Modal's VM runtime (`runtime="vm"`), so each run has its own guest kernel.
Modal starts `pdrun` as root with full capabilities, and `pdrun` then applies the
limits Docker would have applied. If any step fails, the run ends before the
patch is read. Under Modal's gVisor runtime the mounts are refused, so it does
end there.

`pdrun` is started with `exec` beside an idle main process. A main process's own
output goes through Modal's logs, which stored the audio, dropped data from the
log when rate-limited, and took up to 16.6 s for a 30 s render.

| Choice | On Modal | Evidence from a running sandbox |
|---|---|---|
| No network | Kept: `block_network=True` | `[netsend]` gets "unreachable"; no default route; only loopback and unconfigured tunnel interfaces |
| CPU | Kept: 0.5 physical core (1 vCPU) | `/proc/cpuinfo` shows one processor |
| Memory, no swap | Kept: 256 MB VM, about 215 MB visible to the guest | `MemTotal`, `SwapTotal: 0`; memory hog gives `memory_limit` |
| Wall clock | Kept: same supervisor; Modal's `timeout` replaces the host deadline | Endless loop gives `timeout` |
| Processes | Replaced: `RLIMIT_NPROC` 32 for uid 65532, instead of a cgroup limit | `/proc/self/limits` |
| Read-only base image | Replaced: `pdrun` remounts `/` read-only | `/proc/self/mounts`; writes fail |
| Temporary filesystem | Replaced: `pdrun` mounts the 32 MB `/work` tmpfs, and an empty read-only `/dev/shm` | `/proc/self/mounts`; filling it gives "No space left" |
| File size, core dumps, open files | Replaced: `setrlimit` | `/proc/self/limits`; large file gives `file_size_limit` |
| Output size | Kept | Console flood is truncated |
| No host mounts | Kept, except Modal's read-only `/__modal/.debug_shell` | `/proc/mounts` |
| No host logs | Kept, by using `exec` | `modal app logs` is empty after a run |
| Privileges | Replaced: `pdrun` drops uid, groups and all capabilities, and sets `no_new_privs` | `/proc/self/status` |
| Docker's seccomp profile | Replaced by the denylist alone, which is weaker; the VM boundary compensates | `Seccomp: 2` |
| Minimal image | Kept: same Dockerfile | Same image definition |
| Pinned build | Partly kept: digests and commit are pinned, but Modal builds and caches the image itself | Built from the same Dockerfile |
| Supervisor split | Kept | `/proc/1/environ` and `/proc/self/environ` unreadable |
| Cleanup | Kept, slower: if the host process dies, the VM idles until its timeout (wall clock + 30 s) | Killed the host mid-run; no sandbox was left 50 s later |
| Concurrency | Kept, and deployment-wide with Postgres (see [Concurrency](#concurrency)) | 10 at once took 1.2 s in total |
| Shared kernel | Improved: own VM kernel | Guest reports its own kernel |

Runaway patches, 10 runs each: endless loop `timeout` 10/10, console flood
`timeout` and truncated 10/10, memory `memory_limit` 10/10, large file
`file_size_limit` 10/10. The container tests pass on both backends. One
difference: a single allocation larger than the VM's memory is refused, so Pd
reports "out of memory" and continues, where Docker kills it.

Wall time seen by the caller, from one laptop:

| | Runs | p50 | p95 | Max |
|---|---|---|---|---|
| Cold: new client process, 3 s of audio | 25 | 2.56 s | 3.14 s | 3.60 s |
| Warm: same client process, 3 s of audio | 25 | 1.18 s | 1.33 s | 1.44 s |
| Warm, 30 s of audio | 20 | 1.74 s | 2.80 s | 2.92 s |
| Docker, same laptop, warm, 3 s of audio | 25 | 0.24 s | 0.30 s | 0.35 s |

Every run starts a new VM; cold adds client start-up and the app and image
lookup. A worker host that has never held the image was not measured.

Cost: 60 runs of 3 s of audio were billed $0.00124, or **$0.00002 per run**. The
$30 monthly credit covers about 1.4 million of them. A run that hits the default
15 s wall clock costs about $0.0003 at the listed rates.

Before production: set `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` on the Railway
service, and a spending limit on the Modal workspace, since a leaked token could
start other workloads there. If Modal stops being suitable, the next option is a
small VM running Docker with gVisor.

| Variable | Default | |
|---|---|---|
| `PD_SANDBOX_BACKEND` | `docker` | `docker` or `modal`. The agent only runs patches when this is set. |
| `PD_SANDBOX_MODAL_APP` | `pd-sandbox` | Modal app to run in |
| `PD_SANDBOX_IMAGE` | `pd-sandbox:latest` | Image to run |
| `PD_SANDBOX_RUNTIME` | Docker's default | For example `runsc` |
| `PD_SANDBOX_DOCKER` | `docker` on `PATH` | Docker CLI path |
| `PD_SANDBOX_CONCURRENCY` | `2` | Runs at once: across the deployment with a Postgres lock database, otherwise per process |
| `PD_SANDBOX_LOCK_URL` | `DATABASE_URL` | Postgres database whose advisory locks count the runs; a non-Postgres URL means per process only |

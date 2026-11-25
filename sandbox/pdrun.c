/* In-container supervisor: run one untrusted Pd patch, offline, and report.
 *
 *   pdrun <audio-seconds> <wall-seconds> < patch.pd > frame
 *   pdrun hold <seconds>   (do nothing: keeps a Modal sandbox alive for exec)
 *
 * The patch runs in a forked child, so a hang, crash or OOM kill there still
 * leaves this process able to report what happened. The child renders into
 * shared memory, not a file, so a patch cannot overwrite its own recording.
 *
 * Output is one JSON line, then `console_bytes` of console text, then
 * `frames` stereo s16le frames. Only this process holds the real stdout.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <math.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "z_libpd.h"

#define RATE 48000
#define CHANNELS 2
#define BLOCK 64
#define MAX_SECONDS 30
#define MAX_WALL 60
#define MAX_PATCH (1024 * 1024)
#define MAX_CONSOLE (64 * 1024)
#define BACKSTOP 10

typedef struct {
    volatile long frames;
    volatile int finished;
    volatile int load_failed;
    float audio[];
} shared_t;

static void die(const char *why) {
    fprintf(stderr, "pdrun: %s: %s\n", why, strerror(errno));
    _exit(70);
}

static void on_alarm(int sig) { (void)sig; _exit(71); }

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static void write_all(const void *data, size_t len) {
    const char *p = data;
    while (len) {
        ssize_t n = write(STDOUT_FILENO, p, len);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) die("write");
        p += n;
        len -= n;
    }
}

static void limit(int resource, rlim_t value) {
    struct rlimit r = {value, value};
    if (setrlimit(resource, &r)) die("setrlimit");
}

/* Docker applies all of this from outside. A host that starts us as root with
 * none of it (Modal) gets it here instead. Any failure ends the run. */
static void harden(void) {
    if (unshare(CLONE_NEWNS)) die("unshare");
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL)) die("private /");
    unsigned long flags = MS_NOEXEC | MS_NOSUID | MS_NODEV;
    if (mount("tmpfs", "/work", "tmpfs", flags, "size=32m,mode=1777")) die("mount /work");
    if (mount("tmpfs", "/dev/shm", "tmpfs", flags | MS_RDONLY, "size=4k")) die("mount /dev/shm");
    if (mount(NULL, "/", NULL, MS_REMOUNT | MS_BIND | MS_RDONLY, NULL)) die("read-only /");
    limit(RLIMIT_FSIZE, 16777216);
    limit(RLIMIT_NOFILE, 64);
    limit(RLIMIT_CORE, 0);
    limit(RLIMIT_NPROC, 32);
    for (int cap = 0; cap < 64; cap++)
        if (prctl(PR_CAPBSET_DROP, cap) && errno != EINVAL) die("capbset");
    if (setgroups(0, NULL) || setresgid(65532, 65532, 65532) || setresuid(65532, 65532, 65532))
        die("setuid");
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) die("no_new_privs");
}

#if defined(__x86_64__)
#define ARCH AUDIT_ARCH_X86_64
#elif defined(__aarch64__)
#define ARCH AUDIT_ARCH_AARCH64
#else
#error "unsupported architecture"
#endif

/* Calls Pd never needs that are common in kernel exploits, or that would start
 * another program. Adds to the runtime's own filter where there is one. */
static void filter_syscalls(void) {
    static const int denied[] = {
        SYS_execve, SYS_execveat, SYS_ptrace, SYS_process_vm_readv, SYS_process_vm_writev,
        SYS_mount, SYS_umount2, SYS_pivot_root, SYS_chroot, SYS_unshare, SYS_setns,
        SYS_bpf, SYS_perf_event_open, SYS_userfaultfd, SYS_keyctl, SYS_add_key, SYS_request_key,
        SYS_io_uring_setup, SYS_init_module, SYS_finit_module, SYS_kexec_load, SYS_memfd_create,
    };
    enum { N = sizeof denied / sizeof *denied };
    struct sock_filter f[N + 8];
    int n = 0;
    f[n++] = (struct sock_filter)BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch));
    f[n++] = (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, ARCH, 1, 0);
    f[n++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS);
    f[n++] = (struct sock_filter)BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr));
    /* x32 call numbers would dodge the list. */
    f[n++] = (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JGE | BPF_K, 0x40000000, N + 1, 0);
    for (int i = 0; i < N; i++)
        f[n++] = (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, denied[i], N - i, 0);
    f[n++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW);
    f[n++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM);
    struct sock_fprog prog = {n, f};
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) || prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog))
        _exit(75);
}

static void save_patch(void) {
    char *buf = malloc(MAX_PATCH + 1);
    size_t len = 0;
    ssize_t n;
    if (!buf) die("malloc");
    while (len <= MAX_PATCH && (n = read(STDIN_FILENO, buf + len, MAX_PATCH + 1 - len)) > 0)
        len += n;
    if (len > MAX_PATCH) { errno = EFBIG; die("patch"); }
    int fd = open("patch.pd", O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0 || write(fd, buf, len) != (ssize_t)len) die("patch.pd");
    close(fd);
    free(buf);
}

static int hook_on;
static void print_hook(const char *s) {
    if (hook_on && write(STDERR_FILENO, s, strlen(s)) < 0) {}
}

static void run_child(shared_t *sh, long total) {
    /* If memory runs out, the kernel should pick this process. */
    int fd = open("/proc/self/oom_score_adj", O_WRONLY);
    if (fd >= 0) { if (write(fd, "1000", 4) < 0) {} close(fd); }

    filter_syscalls();
    libpd_set_printhook(print_hook);
    if (libpd_init()) _exit(72);
    libpd_set_printhook(print_hook); /* init may have reset it */
    hook_on = 1; /* after init, so Pd's version banner is not in the console */
    libpd_init_audio(CHANNELS, CHANNELS, RATE);
    if (!libpd_openfile("patch.pd", ".")) { sh->load_failed = 1; _exit(73); }
    libpd_start_message(1);
    libpd_add_float(1);
    libpd_finish_message("pd", "dsp");

    float in[BLOCK * CHANNELS] = {0};
    float out[BLOCK * CHANNELS];
    for (long done = 0; done < total; ) {
        long n = total - done < BLOCK ? total - done : BLOCK;
        libpd_process_float(1, in, out);
        memcpy(sh->audio + done * CHANNELS, out, n * CHANNELS * sizeof(float));
        done += n;
        sh->frames = done;
    }
    sh->finished = 1;
    _exit(0);
}

int main(int argc, char **argv) {
    char *end;
    if (argc == 3 && !strcmp(argv[1], "hold")) {
        sleep((unsigned)atoi(argv[2]));
        return 0;
    }
    double seconds = argc > 1 ? strtod(argv[1], &end) : 0;
    double wall = argc > 2 ? strtod(argv[2], &end) : 0;
    if (!(seconds > 0 && seconds <= MAX_SECONDS && wall > 0 && wall <= MAX_WALL)) {
        fprintf(stderr, "usage: pdrun <audio-seconds> <wall-seconds>\n");
        return 64;
    }
    double deadline = now() + wall;

    /* Backstop for everything outside the poll loop. PID 1 ignores signals
     * with default handlers, so the handler must be explicit. */
    signal(SIGALRM, on_alarm);
    alarm((unsigned)wall + BACKSTOP);
    if (getuid() == 0) harden();
    /* Keeps the same-uid child out of our memory and /proc/1/fd. */
    prctl(PR_SET_DUMPABLE, 0);

    if (chdir("/work")) die("chdir /work");
    save_patch();

    long total = (long)ceil(seconds * RATE);
    size_t map_len = sizeof(shared_t) + total * CHANNELS * sizeof(float);
    shared_t *sh = mmap(NULL, map_len, PROT_READ | PROT_WRITE,
                        MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (sh == MAP_FAILED) die("mmap");

    int pipefd[2];
    if (pipe(pipefd)) die("pipe");
    pid_t child = fork();
    if (child < 0) die("fork");
    if (child == 0) {
        alarm(0);
        int null = open("/dev/null", O_RDWR);
        if (null < 0) _exit(74);
        dup2(null, STDIN_FILENO);
        dup2(null, STDOUT_FILENO);
        dup2(pipefd[1], STDERR_FILENO);
        close(null);
        close(pipefd[0]);
        close(pipefd[1]);
        run_child(sh, total);
    }
    close(pipefd[1]);
    /* Non-blocking: never wait on a pipe some descendant still holds. */
    fcntl(pipefd[0], F_SETFL, O_NONBLOCK);

    static char console[MAX_CONSOLE];
    size_t console_len = 0;
    int truncated = 0, timed_out = 0, killed = 0, exited = 0, wstatus = 0;
    char chunk[8192];
    for (;;) {
        struct pollfd p = {pipefd[0], POLLIN, 0};
        poll(&p, 1, 10);
        ssize_t n = 0;
        /* Bounded, so a flood of output cannot delay the deadline check. */
        for (int i = 0; i < 32 && (n = read(pipefd[0], chunk, sizeof chunk)) > 0; i++) {
            size_t keep = MAX_CONSOLE - console_len;
            if ((size_t)n < keep) keep = n;
            memcpy(console + console_len, chunk, keep);
            console_len += keep;
            if (keep < (size_t)n) truncated = 1;
        }
        if (exited) break;
        if (!killed && now() >= deadline) {
            kill(child, SIGKILL);
            killed = timed_out = 1;
        }
        if (waitpid(child, &wstatus, WNOHANG) == child) exited = 1;
        else if (n == 0) usleep(5000); /* pipe closed but child still running */
    }

    int sig = WIFSIGNALED(wstatus) ? WTERMSIG(wstatus) : 0;
    int code = WIFEXITED(wstatus) ? WEXITSTATUS(wstatus) : 128 + sig;
    const char *status;
    /* The cgroup OOM killer is the only other thing that sends SIGKILL. */
    if (sh->finished && !sig && code == 0) status = "ok";
    else if (timed_out && sig == SIGKILL) status = "timeout";
    else if (sig == SIGKILL) status = "memory_limit";
    else if (sig == SIGXFSZ) status = "file_size_limit";
    else if (sig) status = "crashed";
    else if (sh->load_failed) status = "load_failed";
    else status = "patch_exited";

    /* Clamp: the child can write anything into the shared mapping. */
    long frames = sh->frames;
    if (frames < 0) frames = 0;
    if (frames > total) frames = total;

    int16_t *pcm = malloc(frames * CHANNELS * sizeof *pcm + 1);
    if (!pcm) die("malloc");
    float peak = 0;
    long nonfinite = 0;
    for (long i = 0; i < frames * CHANNELS; i++) {
        float v = sh->audio[i];
        if (!isfinite(v)) { nonfinite++; v = 0; }
        if (fabsf(v) > peak) peak = fabsf(v);
        if (v > 1) v = 1;
        if (v < -1) v = -1;
        pcm[i] = (int16_t)lrintf(v * 32767);
    }

    char header[512];
    int hl = snprintf(header, sizeof header,
        "{\"status\":\"%s\",\"exit_code\":%d,\"signal\":%d,\"console_bytes\":%zu,"
        "\"console_truncated\":%s,\"frames\":%ld,\"peak\":%.6g,\"nonfinite_samples\":%ld}\n",
        status, code, sig, console_len, truncated ? "true" : "false",
        frames, (double)peak, nonfinite);
    write_all(header, hl);
    write_all(console, console_len);
    write_all(pcm, frames * CHANNELS * sizeof *pcm);
    return 0;
}

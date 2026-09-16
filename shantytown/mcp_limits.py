"""Delegated MCP children stay below the pane's gaming and memory ceilings.

No sibling systemd units, no uncapped fallback. Enable delegation explicitly on
an exclusively owned scope before provisioning bounded commands. The supervisor
stays in st-runtime and kills the exact child cgroup on EOF, idle or termination.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import uuid

from . import gaming_scopes, panemem


def prepare(root, agent, pane_pid, *, run=subprocess.run):
    scope = panemem.scope_of_pid(pane_pid)
    group = panemem._cgroup_path_of_pid(pane_pid)
    if not scope or not scope.startswith('tmux-spawn-') or not group:
        raise ValueError('MCP delegation requires a private tmux pane scope')
    owned, reason = gaming_scopes.owned_scope(root, agent, pane_pid, group)
    if not owned:
        raise ValueError('MCP delegation refused: ' + reason)
    # Caller decides which pane to prepare; ownership is proven above before
    # changing properties or migrating any process. Parent limits are untouched.
    # Delegate is not a mutable D-Bus property on an existing scope. A
    # scope-specific runtime drop-in plus reload is supported; never edit a
    # slice-wide default. The file disappears on reboot with the transient pane.
    result = run(['systemctl', '--user', 'show', scope, '-p', 'DelegateControllers', '--value'],
                 check=True, capture_output=True, text=True, timeout=10)
    if not {'cpu', 'memory', 'pids'} <= set(result.stdout.split()):
        run(['systemctl', '--user', 'edit', '--runtime', '--drop-in=50-st-mcp.conf',
             '--stdin', scope], input='[Scope]\nDelegate=cpu memory pids\n',
            check=True, capture_output=True, text=True, timeout=10)
        result = run(['systemctl', '--user', 'show', scope, '-p', 'DelegateControllers', '--value'],
                     check=True, capture_output=True, text=True, timeout=10)
        if not {'cpu', 'memory', 'pids'} <= set(result.stdout.split()):
            raise RuntimeError('scope delegation read-back differs')
    base = Path('/sys/fs/cgroup' + group)
    runtime = base / 'st-runtime'
    runtime.mkdir(exist_ok=True)
    # Move the root population first. New children then inherit st-runtime.
    # Retry boundedly for forks during the move; never enable a populated parent.
    for _ in range(8):
        pids = (base / 'cgroup.procs').read_text().split()
        if not pids:
            break
        for pid in pids:
            try:
                (runtime / 'cgroup.procs').write_text(pid)
            except ProcessLookupError:
                continue
    if (base / 'cgroup.procs').read_text().strip():
        raise RuntimeError('scope is still populated; no MCP child may launch')
    (base / 'cgroup.subtree_control').write_text('+cpu +memory +pids')
    required = {'cpu', 'memory', 'pids'}
    if not required <= set((base / 'cgroup.subtree_control').read_text().split()):
        raise RuntimeError('delegated controllers unavailable')
    return base


def child_group(base, cpu_percent=200, memory_bytes=2*1024**3):
    if not {'cpu', 'memory', 'pids'} <= set((base/'cgroup.subtree_control').read_text().split()):
        raise ValueError('MCP scope is not prepared; refusing an unbounded launch')
    group = base / ('st-mcp-' + uuid.uuid4().hex)
    group.mkdir()
    limits = {'cpu.max': f'{cpu_percent * 1000} 100000', 'cpu.weight': '10',
              'memory.max': str(memory_bytes), 'memory.swap.max': '0',
              'memory.oom.group': '1', 'pids.max': '256'}
    try:
        for name, value in limits.items():
            (group/name).write_text(value)
            if (group/name).read_text().strip() != value:
                raise RuntimeError('MCP limit read-back differs: ' + name)
    except BaseException:
        group.rmdir()
        raise
    return group


def relay(command, group, idle_seconds):
    """Opaque stdio relay; only client bytes renew the idle deadline."""
    def enter():
        (group/'cgroup.procs').write_text(str(os.getpid()))
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   preexec_fn=enter, start_new_session=True)
    except BaseException:
        group.rmdir()
        raise
    last_input = time.monotonic()
    stop = False
    old = {}
    def stopping(signum, frame):
        nonlocal stop
        stop = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        old[sig] = signal.signal(sig, stopping)
    selector = selectors.DefaultSelector()
    streams = [sys.stdin.buffer, process.stdout, process.stdin, sys.stdout.buffer]
    blocking = {stream.fileno(): os.get_blocking(stream.fileno()) for stream in streams}
    pending = {process.stdin.fileno(): bytearray(), sys.stdout.buffer.fileno(): bytearray()}
    client_open = server_open = True
    try:
        for fd in blocking:
            os.set_blocking(fd, False)
        while not stop:
            if time.monotonic() - last_input >= idle_seconds:
                print('MCP idle timeout: closing child cgroup', file=sys.stderr)
                return 0
            # Backpressure bounds relay memory while leaving the idle/signal
            # loop responsive when either peer stops reading.
            for key in list(selector.get_map().values()):
                selector.unregister(key.fd)
            if client_open and len(pending[process.stdin.fileno()]) < 1024**2:
                selector.register(sys.stdin.buffer, selectors.EVENT_READ, 'client')
            if server_open and len(pending[sys.stdout.buffer.fileno()]) < 1024**2:
                selector.register(process.stdout, selectors.EVENT_READ, 'server')
            for fd, buffer in pending.items():
                if buffer:
                    selector.register(fd, selectors.EVENT_WRITE, 'write')
            if not client_open and not pending[process.stdin.fileno()]:
                # Keep the file descriptor stable until final cleanup.
                # EOF on the client ends this MCP session and its descendants.
                return 0
            if not server_open and not pending[sys.stdout.buffer.fileno()]:
                return process.poll() or 0
            for key, _ in selector.select(timeout=min(1, idle_seconds)):
                if key.data == 'write':
                    buffer = pending[key.fd]
                    try:
                        count = os.write(key.fd, buffer)
                    except BlockingIOError:
                        continue
                    del buffer[:count]
                    continue
                try:
                    data = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if key.data == 'client':
                    if data:
                        last_input = time.monotonic()
                        pending[process.stdin.fileno()].extend(data)
                    else:
                        client_open = False
                else:
                    if data:
                        pending[sys.stdout.buffer.fileno()].extend(data)
                    else:
                        server_open = False
        return process.poll() or 0
    finally:
        selector.close()
        for fd, value in blocking.items():
            os.set_blocking(fd, value)
        for sig, handler in old.items():
            signal.signal(sig, handler)
        # cgroup.kill reaches detached browser grandchildren; process-group
        # signalling alone cannot. Never target the pane or st-runtime group.
        (group/'cgroup.kill').write_text('1')
        process.wait(timeout=5)
        for _ in range(50):
            try:
                group.rmdir()
                break
            except OSError:
                time.sleep(.02)


def enabled(root, agent):
    path = Path(root)/'provision/mcp-limits.json'
    if not path.exists():
        return False
    policy = json.loads(path.read_text())
    return bool(policy.get('enabled') and agent in policy.get('agents', []))


def prepare_launch(root, agent, panes, session):
    if not enabled(root, agent):
        return
    pid = panes.pane_pid(session)
    if not pid:
        raise ValueError('new pane PID is unavailable')
    scope, why = panemem.resolve_pane_scope(pid)
    if not scope:
        raise ValueError('new pane scope is not exclusive: ' + why)
    prepare(root, agent, int(pid))


def project(data, root, agent):
    policy_path = Path(root)/'provision/mcp-limits.json'
    if not policy_path.exists():
        return data
    policy = json.loads(policy_path.read_text())
    if not policy.get('enabled') or agent not in policy.get('agents', []):
        return data
    cpu, memory, idle = (policy.get('cpu_percent', 200), policy.get('memory_bytes', 2*1024**3),
                         policy.get('idle_seconds', 300))
    if any(type(x) is not int or x <= 0 for x in (cpu, memory, idle)):
        raise ValueError('MCP resource limits must be positive integers')
    out = json.loads(json.dumps(data))
    for server in out.get('mcpServers', out).values():
        if server.get('command'):
            command = [server['command'], *server.get('args', [])]
            server['command'] = sys.executable
            server['args'] = ['-m', 'shantytown.mcp_limits', '--cpu-percent', str(cpu),
                '--memory-bytes', str(memory), '--idle-seconds', str(idle), '--', *command]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--prepare', action='store_true')
    ap.add_argument('--root', type=Path)
    ap.add_argument('--agent')
    ap.add_argument('--pane-pid', type=int)
    ap.add_argument('--cpu-percent', type=int, default=200)
    ap.add_argument('--memory-bytes', type=int, default=2*1024**3)
    ap.add_argument('--idle-seconds', type=int, default=300)
    ap.add_argument('command', nargs=argparse.REMAINDER)
    args = ap.parse_args()
    try:
        if args.prepare:
            if not args.root or not args.agent or not args.pane_pid:
                raise ValueError('prepare requires root, agent and pane-pid')
            print(prepare(args.root, args.agent, args.pane_pid))
            return 0
        if min(args.cpu_percent, args.memory_bytes, args.idle_seconds) <= 0:
            raise ValueError('limits must be positive')
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command:
            raise ValueError('server command required')
        scope = panemem.scope_of_pid(os.getpid())
        group = panemem._cgroup_path_of_pid(os.getpid())
        if not scope or not scope.startswith('tmux-spawn-') or not group:
            raise ValueError('MCP process is outside a private pane scope')
        base = Path('/sys/fs/cgroup'+group)
        return relay(command, child_group(base, args.cpu_percent, args.memory_bytes), args.idle_seconds)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'MCP containment refused: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

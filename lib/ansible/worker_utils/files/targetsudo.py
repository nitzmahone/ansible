# NB: this file must be templated under module_python

import json
import getpass
import inspect
import os
import pty
import secrets
import shlex
import subprocess
import sys
import tty
import typing as t


# OPTIONS = r'''
# TEMPLATE_OPTIONS
# '''

OPTIONS = r'''
{
"become_method": "sudo",
"requires_tty": true
}
'''


def main():
    options = json.loads(OPTIONS)

    if options['become_method'] == 'sudo' and not os.environ.get('ALREADY_RESPAWNED'):
        use_pty = False
        if bool(options.get('requires_tty', False)):

            use_pty = True

        respawn_self(use_pty)

    else:
        print(json.dumps(dict(changed=False, msg=f'hello from {getpass.getuser()}')))


def respawn_self(use_pty=False):
    os.environ['ALREADY_RESPAWNED'] = '1'

    become_id = secrets.token_hex(16)
    pass_prompt = f'[sudo via ansible, key={become_id}] password:'
    b_pass_prompt = pass_prompt.encode()

    success_msg = f'BECOME-SUCCESS-{become_id}'
    b_success_msg = success_msg.encode()

    bootstrap_cmd = "import base64; d = base64.b64decode('{0}').decode(); eval(d)"

    sub_cmd = shlex.join(['echo', success_msg]) + ' && ' + shlex.join([sys.executable] + sys.argv)
    sudo_args = ['sudo', '-p', pass_prompt, '--preserve-env=ALREADY_RESPAWNED', '/bin/sh', '-c', sub_cmd]
    # sudo_args = ['sudo', '-p', pass_prompt, '-u', 'junky', '/bin/sh', '-c', sub_cmd]

    command_line = sudo_args

    # this wouldn't be necessary with ansiballz, since we've already dropped the module code to disk- just run it
    this_module_source = inspect.getsource(sys.modules[__name__])

    if use_pty:
        parent, child = pty.openpty()
        try:
            tty.setraw(child)

            proc = subprocess.Popen(
                command_line,
                stdin=child,
                stdout=child,
                stderr=child,
            )
            os.close(child)

            out = become_processor(parent, b_pass_prompt, b_success_msg, b"FIXME pass")
            while True:
                if not out:
                    try:
                        out = os.read(parent, 4096)
                    except OSError:
                        break

                if not out:
                    break

                sys.stdout.buffer.write(out)
                out = b""

        finally:
            os.close(parent)

        sys.exit(proc.wait())


def become_processor(become_pty: int, prompt: bytes, success: bytes, password: bytes) -> bytes:
    lines: t.List[bytes] = []
    completed_newline = True

    while True:
        data = os.read(become_pty, 4096)
        if not data:
            continue

        last_line_idx = len(lines)
        while data:
            newline_idx = data.find(b"\n")
            if newline_idx == -1:
                newline_idx = len(data)

            if completed_newline:
                lines.append(data[:newline_idx])
            else:
                lines[-1] += data[:newline_idx]

            completed_newline = len(data) > newline_idx
            data = data[newline_idx + 1:]

        extra_lines = len(lines)
        for idx in range(last_line_idx, extra_lines):
            line = lines[idx]

            if prompt in line:
                os.write(become_pty, password + b"\n")

            elif success in line:
                return b"\n".join(lines[idx + 1:])

            else:
                raise Exception(f"Unexpected become output: {line.decode()}")


if __name__ == '__main__':
    main()

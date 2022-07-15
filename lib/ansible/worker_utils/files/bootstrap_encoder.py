import base64
import pathlib
import subprocess
import shlex


def encode_for_dash_c():
    python_payload_bootstrap = pathlib.Path(__file__).parent / 'python_payload_bootstrap.py'
    with open(python_payload_bootstrap, mode='rb') as fd:
        content = base64.b64encode(fd.read()).decode()

    bootstrap_template = f'import base64; exec(base64.b64decode("{content}").decode())'

    return bootstrap_template


def main():

    bootstrap = shlex.join(['python', '-c', encode_for_dash_c()])
    stdin = subprocess.run(['python', str(pathlib.Path(__file__).parent / 'payload_encoder.py')], capture_output=True,
                           check=True).stdout
    cmd = ['ssh', 'junky@localhost', bootstrap]
    print(f'Bootstrapper code is {len(bootstrap)} characters.')
    print(f'Running: {shlex.join(cmd)}')
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    marker = proc.stdout.readline().strip()
    print(f"Received marker {marker}")

    if marker != b'SEND-ME-CODE-NOW':
        stdout, stderr = proc.communicate()
        raise Exception(f"Expected SEND-ME-CODE-NOW but found {marker}\n"
                        f">>> Standard Out\n{stdout.decode()}\n"
                        f">>> Standard Error\n{stderr.decode()}")

    print("Sending code")

    stdout, _ = proc.communicate(stdin)
    print(stdout.decode())


if __name__ == '__main__':
    main()
    #print(encode_for_dash_c())

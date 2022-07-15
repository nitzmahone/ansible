import base64
import os
import sys
import termios

stdin_fileno = sys.stdin.fileno()
saved_flags = None

# FIXME: determine which cases we want to do this handling in a more explicit way
# if os.isatty(stdin_fileno):
#     saved_flags = termios.tcgetattr(stdin_fileno)
#     flags = termios.tcgetattr(stdin_fileno)
#     flags[3] &= ~termios.ECHO
#     termios.tcsetattr(stdin_fileno, termios.TCSANOW, flags)

try:
    # FIXME: determine when we want to signal the controller to send in-band vs allowing it to pipeline ahead of time
    # print('SEND-ME-CODE-NOW', flush=True)

    raw_lines = []

    for line in sys.stdin:
        line = line.strip()

        if not line:
            break

        raw_lines.append(base64.b64decode(line).decode())

    exec(''.join(raw_lines))
finally:
    pass
    # if saved_flags:
    #    termios.tcsetattr(stdin_fileno, termios.TCSANOW, saved_flags)

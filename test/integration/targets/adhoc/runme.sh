#!/usr/bin/env bash

set -eux

# run type tests
ansible -a 'sleep 20' --task-timeout 5 localhost |grep 'The command action failed to execute in the expected time frame (5) and was terminated'

# -a parsing with json
ansible --task-timeout 5 localhost -m command -a '{"cmd": "whoami"}' | grep 'rc=0'

# ensure that legacy deserializer behaves as expected on JSON CLI args (https://github.com/ansible/ansible/issues/82600)
ansible localhost -m debug -a var=fromcli -e '{"fromcli":{"__ansible_unsafe":"{{\"hello\"}}"}}' > "${OUTPUT_DIR}/output.txt" 2>&1
grep '"fromcli": "{{."hello."}}"' "${OUTPUT_DIR}/output.txt"  # ensure that the template was not rendered
grep "Skipped untrusted template" "${OUTPUT_DIR}/output.txt"  # look for the untrusted template warning text

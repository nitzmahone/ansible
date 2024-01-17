#!/usr/bin/env bash

set -eux

unset ANSIBLE_DEPRECATION_WARNINGS

ansible-playbook untrusted_propagation.yml "$@" -e output_dir="${OUTPUT_DIR}"

ANSIBLE_CALLBACK_FORMAT_PRETTY=0 ANSIBLE_WRAP_STDERR=0 ansible-playbook -i hosts output_tests.yml -vvv 2>&1 | tee output.txt

../playbook_output_validator/filter.py actual_stdout.txt actual_stderr.txt < output.txt

# uncomment the following lines to update the test output. don't forget to comment them again :D
#cp -av actual_stdout.txt $JUNIT_OUTPUT_DIR/../../../test/integration/targets/data_tagging_controller/expected_stdout.txt
#cp -av actual_stderr.txt $JUNIT_OUTPUT_DIR/../../../test/integration/targets/data_tagging_controller/expected_stderr.txt

diff -u expected_stdout.txt actual_stdout.txt
diff -u expected_stderr.txt actual_stderr.txt

#!/usr/bin/env bash

set -eux

ansible-playbook -i ../../inventory play.yml "$@"

ansible-playbook -i ../../inventory output_validation_tests.yml

# FIXME: capture output and diff against known-good values

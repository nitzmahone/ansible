#!/usr/bin/env bash

set -eux

diff -uw <(ansible-inventory -i inventory.sh --list --export) inventory.json

ansible-inventory -i broken-inventory.py --list --export 2> broken.err

grep 'Inventory script result could not be parsed as JSON' broken.err
grep 'this is stderr' broken.err

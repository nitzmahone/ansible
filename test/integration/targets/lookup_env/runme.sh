#!/bin/sh

set -eux

unset USR

USR='' ansible-playbook usr_set.yml -i ../../inventory "${@}"

ansible-playbook usr_not_set.yml -i ../../inventory "${@}"

#!/bin/sh

set -eux

unset USR

USR='' ansible-playbook usr_defined.yml -i ../../inventory "${@}"

ansible-playbook usr_not_defined.yml -i ../../inventory "${@}"

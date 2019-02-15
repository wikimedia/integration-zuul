#!/bin/bash

# Point at deployed dirs.
deploy_dir="${PWD}/../.."
venv="${deploy_dir}/venv"

# Install python libs.
cd $deploy_dir
mkdir -p $venv
python3 -m venv $venv
[ -f $PWD/wheels/pip-*.whl ] && $venv/bin/pip3 install --use-wheel --no-deps $PWD/wheels/pip-*.whl
$venv/bin/pip3 install --use-wheel --no-deps $PWD/wheels/*.whl

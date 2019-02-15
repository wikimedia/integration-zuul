#!/usr/bin/env bash

# Point at deployed dirs.
DEPLOY_DIR=/srv/deployment/integration/zuul
VENV="${DEPLOY_DIR}/venv"

# Install python libs.
cd $DEPLOY_DIR
mkdir -p $VENV
python3 -m venv $VENV
[ -f $DEPLOY_DIR/wheels/pip-*.whl ] && $VENV/bin/pip3 install --use-wheel --no-deps $DEPLOY_DIR/wheels/pip-*.whl
$VENV/bin/pip3 install --use-wheel --no-deps $DEPLOY_DIR/wheels/*.whl

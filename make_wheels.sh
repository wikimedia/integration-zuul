#!/usr/bin/env bash
# Build wheels for distribution
set -o errexit
set -o nounset
set -o pipefail

BASE=$(realpath $(dirname $0))
BUILD=${BASE}/build
VENV=${BUILD}/venv
ZUUL=${BASE}
WHEEL_DIR=${BASE}/wheels
REQUIREMENTS=${BASE}/requirements.txt

PIP=${VENV}/bin/pip

mkdir -p $VENV
virtualenv --python python3 $VENV || /bin/true
$PIP install -r ${ZUUL}/requirements.txt
$PIP freeze --local --requirement $REQUIREMENTS > $REQUIREMENTS



$PIP install wheel
$PIP wheel --find-links $WHEEL_DIR \
    --wheel-dir $WHEEL_DIR \
    --requirement $REQUIREMENTS

cd _build/venv/lib/python3.5/site-packages

ln -s ../../../../../zuul ./zuul

cd ../../../../../


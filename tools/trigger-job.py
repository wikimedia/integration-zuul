#!/usr/bin/env python
# Copyright 2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

# This script can be used to manually trigger a job in the same way that
# Zuul does.  At the moment, it only supports the post set of Zuul
# parameters.

import argparse
import sys
import time
import json
from uuid import uuid4

import gear


def main():
    c = gear.Client()

    parser = argparse.ArgumentParser(description='Trigger a Zuul job.')
    parser.add_argument('--job', dest='job', required=True,
                        help='Job Name')
    parser.add_argument('--project', dest='project', required=True,
                        help='Project name')
    parser.add_argument('--pipeline', dest='pipeline', default='release',
                        help='Zuul pipeline')
    parser.add_argument('--branch', dest='branch',
                        help='Branch name')
    parser.add_argument('--url', dest='url',
                        default='https://gerrit.wikimedia.org/r/p',
                        help='Git base URL')
    args = parser.parse_args()

    data = {'ZUUL_PIPELINE': args.pipeline,
            'ZUUL_PROJECT': args.project,
            'ZUUL_UUID': str(uuid4().hex),
            'ZUUL_REF': args.branch,
            'ZUUL_BRANCH': args.branch,
            'ZUUL_REFNAME': args.branch,
            'ZUUL_COMMIT': args.branch,
            'ZUUL_URL': args.url,
            'OFFLINE_NODE_WHEN_COMPLETE': 1,
            }

    c.addServer('127.0.0.1', 4730)
    c.waitForServer()

    job = gear.Job("build:%s" % args.job,
                   json.dumps(data),
                   unique=data['ZUUL_UUID'])
    c.submitJob(job)

    while not job.complete:
        sys.stdout.write('.')
        sys.stdout.flush()
        time.sleep(1)
    if job.failure:
        print "Job failed!"
        return 1
    print "Job passed"
    print job.data

if __name__ == '__main__':
    sys.exit(main())

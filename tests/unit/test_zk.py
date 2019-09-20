# Copyright 2019 Red Hat, Inc.
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


import testtools

import zuul.zk
from zuul import model

from tests.base import BaseTestCase, ChrootedKazooFixture


class TestZK(BaseTestCase):

    def setUp(self):
        super().setUp()

        self.zk_chroot_fixture = self.useFixture(
            ChrootedKazooFixture(self.id()))
        self.zk_config = '%s:%s%s' % (
            self.zk_chroot_fixture.zookeeper_host,
            self.zk_chroot_fixture.zookeeper_port,
            self.zk_chroot_fixture.zookeeper_chroot)

        self.zk = zuul.zk.ZooKeeper(enable_cache=True)
        self.addCleanup(self.zk.disconnect)
        self.zk.connect(self.zk_config)

    def _createRequest(self):
        req = model.HoldRequest()
        req.count = 1
        req.reason = 'some reason'
        req.expiration = 1
        return req

    def test_hold_requests_api(self):
        # Test no requests returns empty list
        self.assertEqual([], self.zk.getHoldRequests())

        # Test get on non-existent request is None
        self.assertIsNone(self.zk.getHoldRequest('anything'))

        # Test creating a new request
        req1 = self._createRequest()
        self.zk.storeHoldRequest(req1)
        self.assertIsNotNone(req1.id)
        self.assertEqual(1, len(self.zk.getHoldRequests()))

        # Test getting the request
        req2 = self.zk.getHoldRequest(req1.id)
        self.assertEqual(req1.toDict(), req2.toDict())

        # Test updating the request
        req2.reason = 'a new reason'
        self.zk.storeHoldRequest(req2)
        req2 = self.zk.getHoldRequest(req2.id)
        self.assertNotEqual(req1.reason, req2.reason)

        # Test lock operations
        self.zk.lockHoldRequest(req2, blocking=False)
        with testtools.ExpectedException(
            zuul.zk.LockException,
            "Timeout trying to acquire lock .*"
        ):
            self.zk.lockHoldRequest(req2, blocking=True, timeout=2)
        self.zk.unlockHoldRequest(req2)
        self.assertIsNone(req2.lock)

        # Test deleting the request
        self.zk.deleteHoldRequest(req1)
        self.assertEqual([], self.zk.getHoldRequests())

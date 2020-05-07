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

from tests.base import (
    ZuulTestCase,
    simple_layout,
)


class TestSerial(ZuulTestCase):
    tenant_config_file = 'config/single-tenant/main.yaml'

    @simple_layout('layouts/serial.yaml')
    def test_deploy_window(self):
        self.executor_server.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        A.setMerged()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()
        # The gerrit upstream repo simulation isn't perfect -- when
        # change A is merged above, the master ref is updated to point
        # to that change, it doesn't actually "merge" it.  The same is
        # true for B, so if it didn't have A in its git history, then
        # A would not appear in the jobs run for B.  We simulate the
        # correct situation by setting A as the git parent of B.
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B',
                                           parent='refs/changes/1/1/1')
        B.setMerged()
        self.fake_gerrit.addEvent(B.getChangeMergedEvent())
        self.waitUntilSettled()

        self.assertEqual(len(self.builds), 2)
        self.assertTrue(self.builds[0].hasChanges(A))
        self.assertTrue(self.builds[1].hasChanges(A))
        self.assertFalse(self.builds[0].hasChanges(B))
        self.assertFalse(self.builds[1].hasChanges(B))

        self.executor_server.release()
        self.waitUntilSettled()

        self.assertEqual(len(self.builds), 2)
        self.assertTrue(self.builds[0].hasChanges(A))
        self.assertTrue(self.builds[1].hasChanges(A))
        self.assertTrue(self.builds[0].hasChanges(B))
        self.assertTrue(self.builds[1].hasChanges(B))

        self.executor_server.release()
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1)
        self.assertEqual(B.reported, 1)
        self.assertHistory([
            dict(name='job1', result='SUCCESS', changes='1,1'),
            dict(name='job2', result='SUCCESS', changes='1,1'),
            dict(name='job1', result='SUCCESS', changes='2,1'),
            dict(name='job2', result='SUCCESS', changes='2,1'),
        ], ordered=False)

    @simple_layout('layouts/serial.yaml')
    def test_deploy_shared(self):
        # Same as test_deploy_window but with two separate projects
        # sharing a queue.
        self.executor_server.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        A.setMerged()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()
        B = self.fake_gerrit.addFakeChange('org/project2', 'master', 'B')
        B.setMerged()
        self.fake_gerrit.addEvent(B.getChangeMergedEvent())
        self.waitUntilSettled()

        self.assertEqual(len(self.builds), 1)
        self.assertTrue(self.builds[0].hasChanges(A))

        self.executor_server.release()
        self.waitUntilSettled()

        self.assertEqual(len(self.builds), 1)
        self.assertTrue(self.builds[0].hasChanges(B))

        self.executor_server.release()
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1)
        self.assertEqual(B.reported, 1)
        self.assertHistory([
            dict(name='job1', result='SUCCESS', changes='1,1'),
            dict(name='job1', result='SUCCESS', changes='2,1'),
        ], ordered=False)

    @simple_layout('layouts/serial.yaml')
    def test_deploy_unshared(self):
        # Test two projects which don't share a queue.
        self.executor_server.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        A.setMerged()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        B = self.fake_gerrit.addFakeChange('org/project1', 'master', 'B')
        B.setMerged()
        self.fake_gerrit.addEvent(B.getChangeMergedEvent())
        self.waitUntilSettled()

        self.assertEqual(len(self.builds), 3)
        self.assertTrue(self.builds[0].hasChanges(A))
        self.assertTrue(self.builds[1].hasChanges(A))
        self.assertTrue(self.builds[2].hasChanges(B))
        self.assertFalse(self.builds[2].hasChanges(A))

        self.executor_server.release()
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1)
        self.assertEqual(B.reported, 1)
        self.assertHistory([
            dict(name='job1', result='SUCCESS', changes='1,1'),
            dict(name='job2', result='SUCCESS', changes='1,1'),
            dict(name='job1', result='SUCCESS', changes='2,1'),
        ], ordered=False)

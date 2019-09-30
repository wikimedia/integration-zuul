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

from zuul.model import Change, TriggerEvent, EventFilter, RefFilter


class PullRequest(Change):
    def __init__(self, project):
        super(PullRequest, self).__init__(project)


class GitlabTriggerEvent(TriggerEvent):
    def __init__(self):
        super(GitlabTriggerEvent, self).__init__()


class GitlabEventFilter(EventFilter):
    def __init__(self, trigger):
        super(GitlabEventFilter, self).__init__()


# The RefFilter should be understood as RequireFilter (it maps to
# pipeline requires definition)
class GitlabRefFilter(RefFilter):
    def __init__(self, connection_name):
        RefFilter.__init__(self, connection_name)

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

import re

from zuul.model import Change, TriggerEvent, EventFilter, RefFilter


class MergeRequest(Change):
    def __init__(self, project):
        super(MergeRequest, self).__init__(project)
        self.updated_at = None

    def __repr__(self):
        r = ['<Change 0x%x' % id(self)]
        if self.project:
            r.append('project: %s' % self.project)
        if self.number:
            r.append('number: %s' % self.number)
        if self.patchset:
            r.append('patchset: %s' % self.patchset)
        if self.updated_at:
            r.append('updated: %s' % self.updated_at)
        if self.is_merged:
            r.append('state: merged')
        if self.open:
            r.append('state: open')
        return ' '.join(r) + '>'

    def isUpdateOf(self, other):
        if (self.project == other.project and
            hasattr(other, 'number') and self.number == other.number and
            hasattr(other, 'updated_at') and
            self.updated_at > other.updated_at):
            return True
        return False


class GitlabTriggerEvent(TriggerEvent):
    def __init__(self):
        super(GitlabTriggerEvent, self).__init__()
        self.trigger_name = 'gitlab'
        self.title = None
        self.action = None
        self.change_number = None

    def _repr(self):
        r = [super(GitlabTriggerEvent, self)._repr()]
        if self.action:
            r.append("action:%s" % self.action)
        r.append("project:%s" % self.canonical_project_name)
        if self.change_number:
            r.append("mr:%s" % self.change_number)
        return ' '.join(r)

    def isPatchsetCreated(self):
        if self.type == 'gl_pull_request':
            return self.action in ['opened', 'changed']
        return False


class GitlabEventFilter(EventFilter):
    def __init__(self, trigger, types=[], actions=[], comments=[]):
        super(GitlabEventFilter, self).__init__(self)
        self._types = types
        self.types = [re.compile(x) for x in types]
        self.actions = actions
        self._comments = comments
        self.comments = [re.compile(x) for x in comments]

    def __repr__(self):
        ret = '<GitlabEventFilter'

        if self._types:
            ret += ' types: %s' % ', '.join(self._types)
        if self.actions:
            ret += ' actions: %s' % ', '.join(self.actions)
        if self._comments:
            ret += ' comments: %s' % ', '.join(self._comments)
        ret += '>'

        return ret

    def matches(self, event, change):
        matches_type = False
        for etype in self.types:
            if etype.match(event.type):
                matches_type = True
        if self.types and not matches_type:
            return False

        matches_action = False
        for action in self.actions:
            if (event.action == action):
                matches_action = True
        if self.actions and not matches_action:
            return False

        matches_comment_re = False
        for comment_re in self.comments:
            if (event.comment is not None and
                comment_re.search(event.comment)):
                matches_comment_re = True
        if self.comments and not matches_comment_re:
            return False

        return True


# The RefFilter should be understood as RequireFilter (it maps to
# pipeline requires definition)
class GitlabRefFilter(RefFilter):
    def __init__(self, connection_name):
        RefFilter.__init__(self, connection_name)

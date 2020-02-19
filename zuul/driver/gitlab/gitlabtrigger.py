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

import logging
from zuul.trigger import BaseTrigger


class GitlabTrigger(BaseTrigger):
    name = 'gitlab'
    log = logging.getLogger("zuul.trigger.GitlabTrigger")

    def getEventFilters(self, trigger_config):
        efilters = []
        return efilters

    def onPullRequest(self, payload):
        pass


def getSchema():
    gitlab_trigger = {}
    return gitlab_trigger

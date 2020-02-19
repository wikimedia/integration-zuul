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
import voluptuous as v

from zuul.reporter import BaseReporter
from zuul.lib.logutil import get_annotated_logger
from zuul.driver.gitlab.gitlabsource import GitlabSource


class GitlabReporter(BaseReporter):
    """Sends off reports to Gitlab."""

    name = 'gitlab'
    log = logging.getLogger("zuul.GitlabReporter")

    def __init__(self, driver, connection, pipeline, config=None):
        super(GitlabReporter, self).__init__(driver, connection, config)
        self._create_comment = self.config.get('comment', True)

    def report(self, item):
        """Report on an event."""
        if not isinstance(item.change.project.source, GitlabSource):
            return

        if item.change.project.source.connection.canonical_hostname != \
                self.connection.canonical_hostname:
            return

        if hasattr(item.change, 'number'):
            if self._create_comment:
                self.addMRComment(item)

    def addMRComment(self, item, comment=None):
        log = get_annotated_logger(self.log, item.event)
        message = comment or self._formatItemReport(item)
        project = item.change.project.name
        pr_number = item.change.number
        log.debug('Reporting change %s, params %s, message: %s',
                  item.change, self.config, message)
        self.connection.commentMR(project, pr_number, message,
                                  event=item.event)

    def mergePull(self, item):
        raise NotImplementedError()

    def getSubmitAllowNeeds(self):
        return []


def getSchema():
    gitlab_reporter = v.Schema({
        'comment': bool,
    })
    return gitlab_reporter

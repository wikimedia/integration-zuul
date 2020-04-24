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

from abc import ABCMeta

from zuul import model
from zuul.lib.logutil import get_annotated_logger
from zuul.manager import PipelineManager, StaticChangeQueueContextManager
from zuul.manager import DynamicChangeQueueContextManager


class SharedQueuePipelineManager(PipelineManager, metaclass=ABCMeta):
    """Intermediate class that adds the shared-queue behavior.

    This is not a full pipeline manager; it just adds the shared-queue
    behavior to the base class and is used by the dependent and serial
    managers.
    """

    changes_merge = False

    def buildChangeQueues(self, layout):
        self.log.debug("Building shared change queues")
        change_queues = {}
        tenant = self.pipeline.tenant
        layout_project_configs = layout.project_configs

        for project_name, project_configs in layout_project_configs.items():
            (trusted, project) = tenant.getProject(project_name)
            queue_name = None
            project_in_pipeline = False
            for project_config in layout.getAllProjectConfigs(project_name):
                project_pipeline_config = project_config.pipelines.get(
                    self.pipeline.name)
                if project_pipeline_config is None:
                    continue
                project_in_pipeline = True
                queue_name = project_pipeline_config.queue_name
                if queue_name:
                    break
            if not project_in_pipeline:
                continue
            if queue_name and queue_name in change_queues:
                change_queue = change_queues[queue_name]
            else:
                change_queue = self.constructChangeQueue(queue_name)
                if queue_name:
                    # If this is a named queue, keep track of it in
                    # case it is referenced again.  Otherwise, it will
                    # have a name automatically generated from its
                    # constituent projects.
                    change_queues[queue_name] = change_queue
                self.pipeline.addQueue(change_queue)
                self.log.debug("Created queue: %s" % change_queue)
            change_queue.addProject(project)
            self.log.debug("Added project %s to queue: %s" %
                           (project, change_queue))

    def getChangeQueue(self, change, event, existing=None):
        log = get_annotated_logger(self.log, event)

        # Ignore the existing queue, since we can always get the correct queue
        # from the pipeline. This avoids enqueuing changes in a wrong queue
        # e.g. during re-configuration.
        queue = self.pipeline.getQueue(change.project)
        if queue:
            return StaticChangeQueueContextManager(queue)
        else:
            # There is no existing queue for this change. Create a
            # dynamic one for this one change's use
            change_queue = model.ChangeQueue(self.pipeline, dynamic=True)
            change_queue.addProject(change.project)
            self.pipeline.addQueue(change_queue)
            log.debug("Dynamically created queue %s", change_queue)
            return DynamicChangeQueueContextManager(change_queue)

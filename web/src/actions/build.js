// Copyright 2018 Red Hat, Inc
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may
// not use this file except in compliance with the License. You may obtain
// a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
// License for the specific language governing permissions and limitations
// under the License.

import Axios from 'axios'

import * as API from '../api'

export const BUILD_FETCH_REQUEST = 'BUILD_FETCH_REQUEST'
export const BUILD_FETCH_SUCCESS = 'BUILD_FETCH_SUCCESS'
export const BUILD_FETCH_FAIL = 'BUILD_FETCH_FAIL'

export const BUILDSET_FETCH_REQUEST = 'BUILDSET_FETCH_REQUEST'
export const BUILDSET_FETCH_SUCCESS = 'BUILDSET_FETCH_SUCCESS'
export const BUILDSET_FETCH_FAIL =    'BUILDSET_FETCH_FAIL'

export const BUILD_OUTPUT_REQUEST = 'BUILD_OUTPUT_FETCH_REQUEST'
export const BUILD_OUTPUT_SUCCESS = 'BUILD_OUTPUT_FETCH_SUCCESS'
export const BUILD_OUTPUT_FAIL = 'BUILD_OUTPUT_FETCH_FAIL'

export const BUILD_MANIFEST_REQUEST = 'BUILD_MANIFEST_FETCH_REQUEST'
export const BUILD_MANIFEST_SUCCESS = 'BUILD_MANIFEST_FETCH_SUCCESS'
export const BUILD_MANIFEST_FAIL = 'BUILD_MANIFEST_FETCH_FAIL'

export const requestBuild = () => ({
  type: BUILD_FETCH_REQUEST
})

export const receiveBuild = (buildId, build) => ({
  type: BUILD_FETCH_SUCCESS,
  buildId: buildId,
  build: build,
  receivedAt: Date.now()
})

const failedBuild = (error, url) => {
  error.url = url
  return {
    type: BUILD_FETCH_FAIL,
    error
  }
}

export const requestBuildOutput = () => ({
  type: BUILD_OUTPUT_REQUEST
})

// job-output processing functions
export function renderTree(tenant, build, path, obj, textRenderer, defaultRenderer) {
  const node = {}
  let name = obj.name

  if ('children' in obj && obj.children) {
    node.nodes = obj.children.map(
      n => renderTree(tenant, build, path+obj.name+'/', n,
                     textRenderer, defaultRenderer))
  }
  if (obj.mimetype === 'application/directory') {
    name = obj.name + '/'
  } else {
    node.icon = 'fa fa-file-o'
  }

  let log_url = build.log_url
  if (log_url.endsWith('/')) {
    log_url = log_url.slice(0, -1)
  }
  if (obj.mimetype === 'text/plain') {
    node.text = textRenderer(tenant, build, path, name, log_url, obj)
  } else {
    node.text = defaultRenderer(log_url, path, name, obj)
  }
  return node
}

export function didTaskFail(task) {
  if (task.failed) {
    return true
  }
  if (task.results) {
    for (let result of task.results) {
      if (didTaskFail(result)) {
        return true
      }
    }
  }
  return false
}

export function hasInterestingKeys (obj, keys) {
  return Object.entries(obj).filter(
    ([k, v]) => (keys.includes(k) && v !== '')
  ).length > 0
}

export function findLoopLabel(item) {
  const label = item._ansible_item_label
  return typeof(label) === 'string' ? label : ''
}

export function shouldIncludeKey(key, value, ignore_underscore, included) {
  if (ignore_underscore && key[0] === '_') {
    return false
  }
  if (included) {
    if (!included.includes(key)) {
      return false
    }
    if (value === '') {
      return false
    }
  }
  return true
}

export function makeTaskPath (path) {
  return path.join('/')
}

export function taskPathMatches (ref, test) {
  if (test.length < ref.length)
    return false
  for (let i=0; i < ref.length; i++) {
    if (ref[i] !== test[i])
      return false
  }
  return true
}


const receiveBuildOutput = (buildId, output) => {
  const hosts = {}
  // Compute stats
  output.forEach(phase => {
    Object.entries(phase.stats).forEach(([host, stats]) => {
      if (!hosts[host]) {
        hosts[host] = stats
        hosts[host].failed = []
      } else {
        hosts[host].changed += stats.changed
        hosts[host].failures += stats.failures
        hosts[host].ok += stats.ok
      }
      if (stats.failures > 0) {
        // Look for failed tasks
        phase.plays.forEach(play => {
          play.tasks.forEach(task => {
            if (task.hosts[host]) {
              if (task.hosts[host].results &&
                  task.hosts[host].results.length > 0) {
                task.hosts[host].results.forEach(result => {
                  if (result.failed) {
                    result.name = task.task.name
                    hosts[host].failed.push(result)
                  }
                })
              } else if (task.hosts[host].rc || task.hosts[host].failed) {
                let result = task.hosts[host]
                result.name = task.task.name
                hosts[host].failed.push(result)
              }
            }
          })
        })
      }
    })
  })

  // Identify all of the hosttasks (and therefore tasks, plays, and
  // playbooks) which have failed.  The errorIds are either task or
  // play uuids, or the phase+index for the playbook.  Since they are
  // different formats, we can store them in the same set without
  // collisions.
  const errorIds = new Set()
  output.forEach(playbook => {
    playbook.plays.forEach(play => {
      play.tasks.forEach(task => {
        Object.entries(task.hosts).forEach(([, host]) => {
          if (didTaskFail(host)) {
            errorIds.add(task.task.id)
            errorIds.add(play.play.id)
            errorIds.add(playbook.phase + playbook.index)
          }
        })
      })
    })
  })

  return {
    type: BUILD_OUTPUT_SUCCESS,
    buildId: buildId,
    hosts: hosts,
    output: output,
    errorIds: errorIds,
    receivedAt: Date.now()
  }
}

const failedBuildOutput = (error, url) => {
  error.url = url
  return {
    type: BUILD_OUTPUT_FAIL,
    error
  }
}

export const requestBuildManifest = () => ({
  type: BUILD_MANIFEST_REQUEST
})

const receiveBuildManifest = (buildId, manifest) => {
  const index = {}

  const renderNode = (root, object) => {
    const path = root + '/' + object.name

    if ('children' in object && object.children) {
      object.children.map(n => renderNode(path, n))
    } else {
      index[path] = object
    }
  }

  manifest.tree.map(n => renderNode('', n))
  return {
    type: BUILD_MANIFEST_SUCCESS,
    buildId: buildId,
    manifest: {tree: manifest.tree, index: index},
    receivedAt: Date.now()
  }
}

const failedBuildManifest = (error, url) => {
  error.url = url
  return {
    type: BUILD_MANIFEST_FAIL,
    error
  }
}

export const fetchBuild = (tenant, buildId, state, force) => dispatch => {
  const build = state.build.builds[buildId]
  if (!force && build) {
    return Promise.resolve()
  }
  dispatch(requestBuild())
  return API.fetchBuild(tenant.apiPrefix, buildId)
    .then(response => {
      dispatch(receiveBuild(buildId, response.data))
    })
    .catch(error => dispatch(failedBuild(error, tenant.apiPrefix)))
}

const fetchBuildOutput = (buildId, state, force) => dispatch => {
  const build = state.build.builds[buildId]
  const url = build.log_url.substr(0, build.log_url.lastIndexOf('/') + 1)
  if (!force && build.output) {
    return Promise.resolve()
  }
  dispatch(requestBuildOutput())
  return Axios.get(url + 'job-output.json.gz')
    .then(response => dispatch(receiveBuildOutput(buildId, response.data)))
    .catch(error => {
      if (!error.request) {
        throw error
      }
      // Try without compression
      Axios.get(url + 'job-output.json')
        .then(response => dispatch(receiveBuildOutput(
          buildId, response.data)))
    })
    .catch(error => dispatch(failedBuildOutput(error, url)))
}

export const fetchBuildManifest = (buildId, state, force) => dispatch => {
  const build = state.build.builds[buildId]
  if (!force && build.manifest) {
    return Promise.resolve()
  }

  dispatch(requestBuildManifest())
  for (let artifact of build.artifacts) {
    if ('metadata' in artifact &&
        'type' in artifact.metadata &&
        artifact.metadata.type === 'zuul_manifest') {
      return Axios.get(artifact.url)
        .then(manifest => {
          dispatch(receiveBuildManifest(buildId, manifest.data))
        })
        .catch(error => dispatch(failedBuildManifest(error, artifact.url)))
    }
  }
  dispatch(failedBuildManifest('no manifest found'))
}

export const fetchBuildIfNeeded = (tenant, buildId, force) => (dispatch, getState) => {
  dispatch(fetchBuild(tenant, buildId, getState(), force))
    .then(() => {
      dispatch(fetchBuildOutput(buildId, getState(), force))
      dispatch(fetchBuildManifest(buildId, getState(), force))
    })
}

export const requestBuildset = () => ({
  type: BUILDSET_FETCH_REQUEST
})

export const receiveBuildset = (buildsetId, buildset) => ({
  type: BUILDSET_FETCH_SUCCESS,
  buildsetId: buildsetId,
  buildset: buildset,
  receivedAt: Date.now()
})

const failedBuildset = error => ({
  type: BUILDSET_FETCH_FAIL,
  error
})

const fetchBuildset = (tenant, buildset) => dispatch => {
  dispatch(requestBuildset())
  return API.fetchBuildset(tenant.apiPrefix, buildset)
    .then(response => {
      response.data.builds.forEach(build => {
        dispatch(receiveBuild(build.uuid, build))
      })
      dispatch(receiveBuildset(buildset, response.data))
    })
    .catch(error => dispatch(failedBuildset(error)))
}

const shouldFetchBuildset = (buildsetId, state) => {
  const buildset = state.build.buildsets[buildsetId]
  if (!buildset) {
    return true
  }
  if (buildset.isFetching) {
    return false
  }
  return false
}

export const fetchBuildsetIfNeeded = (tenant, buildsetId, force) => (
  dispatch, getState) => {
    if (force || shouldFetchBuildset(buildsetId, getState())) {
      return dispatch(fetchBuildset(tenant, buildsetId))
    }
}

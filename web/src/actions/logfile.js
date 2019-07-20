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

import {fetchBuild, fetchBuildManifest} from './build'

export const LOGFILE_FETCH_REQUEST = 'LOGFILE_FETCH_REQUEST'
export const LOGFILE_FETCH_SUCCESS = 'LOGFILE_FETCH_SUCCESS'
export const LOGFILE_FETCH_FAIL = 'LOGFILE_FETCH_FAIL'

export const requestLogfile = (url) => ({
  type: LOGFILE_FETCH_REQUEST,
  url: url,
})

const receiveLogfile = (data) => ({
    type: LOGFILE_FETCH_SUCCESS,
    data: data,
    receivedAt: Date.now()
})

const failedLogfile = error => ({
  type: LOGFILE_FETCH_FAIL,
  error
})

const fetchLogfile = (buildId, file, state, force) => dispatch => {
  const build = state.build.builds[buildId]
  const item = build.manifest.index['/' + file]
  const url = build.log_url + item.name

  if (!force && state.logfile.url === url) {
    return Promise.resolve()
  }
  dispatch(requestLogfile())
  if (item.mimetype === 'text/plain') {
    return Axios.get(url)
      .then(response => dispatch(receiveLogfile(response.data)))
      .catch(error => dispatch(failedLogfile(error)))
  }
  dispatch(failedLogfile(null))
}

export const fetchLogfileIfNeeded = (tenant, buildId, file, force) => (dispatch, getState) => {
  dispatch(fetchBuild(tenant, buildId, getState(), force))
    .then(() => {
      dispatch(fetchBuildManifest(buildId, getState(), force))
        .then(() => {
          dispatch(fetchLogfile(buildId, file, getState(), force))
        })
    })
}

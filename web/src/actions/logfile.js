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

const SYSLOGDATE = '\\w+\\s+\\d+\\s+\\d{2}:\\d{2}:\\d{2}((\\.|\\,)\\d{3,6})?'
const DATEFMT = '\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}((\\.|\\,)\\d{3,6})?'
const STATUSFMT = '(DEBUG|INFO|WARNING|ERROR|TRACE|AUDIT|CRITICAL)'

const severityMap = {
  DEBUG: 1,
  INFO: 2,
  WARNING: 3,
  ERROR: 4,
  TRACE: 5,
  AUDIT: 6,
  CRITICAL: 7,
}

const OSLO_LOGMATCH = new RegExp(`^(${DATEFMT})(( \\d+)? (${STATUSFMT}).*)`)
const SYSTEMD_LOGMATCH = new RegExp(`^(${SYSLOGDATE})( (\\S+) \\S+\\[\\d+\\]\\: (${STATUSFMT})?.*)`)

const receiveLogfile = (data) => {

  const out = data.split(/\r?\n/).map((line, idx) => {
    let m = null
    let sev = null

    m = SYSTEMD_LOGMATCH.exec(line)
    if (m) {
      sev = severityMap[m[7]]
    } else {
      OSLO_LOGMATCH.exec(line)
      if (m) {
        sev = severityMap[m[7]]
      }
    }

    return {
      text: line,
      index: idx+1,
      severity: sev
    }
  })
  return {
    type: LOGFILE_FETCH_SUCCESS,
    data: out,
    receivedAt: Date.now()
  }
}

const failedLogfile = (error, url) => {
  error.url = url
  return {
    type: LOGFILE_FETCH_FAIL,
    error
  }
}

const fetchLogfile = (buildId, file, state, force) => dispatch => {
  const build = state.build.builds[buildId]
  const item = build.manifest.index['/' + file]

  if (!item)
    dispatch(failedLogfile(null))
  const url = build.log_url + file

  if (!force && state.logfile.url === url) {
    return Promise.resolve()
  }
  dispatch(requestLogfile())
  if (item.mimetype === 'text/plain') {
    return Axios.get(url, {transformResponse: []})
      .then(response => dispatch(receiveLogfile(response.data)))
      .catch(error => dispatch(failedLogfile(error, url)))
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

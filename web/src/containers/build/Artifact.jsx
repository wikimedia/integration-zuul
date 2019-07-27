// Copyright 2019 Red Hat, Inc
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

import React from 'react'
import PropTypes from 'prop-types'


class Artifact extends React.Component {
  static propTypes = {
    artifact: PropTypes.object.isRequired
  }

  render() {
    const { artifact } = this.props
    return (
      <table className="table table-striped table-bordered" style={{width:'50%'}}>
        <thead>
          <tr><th colSpan="2"><a href={artifact.url}>{artifact.name}</a></th></tr>
        </thead>
        <tbody>
          {Object.keys(artifact.metadata).map(key => (
            <tr key={key}>
              <td>{key}</td>
              <td style={{width:'100%'}}>{artifact.metadata[key]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }
}

export default Artifact

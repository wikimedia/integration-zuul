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

import * as React from 'react'
import PropTypes from 'prop-types'
import { connect } from 'react-redux'
import { Link } from 'react-router-dom'
import { Table } from 'patternfly-react'


class JobsList extends React.Component {
  static propTypes = {
    tenant: PropTypes.object,
    jobs: PropTypes.array,
  }

  render () {
    const { jobs } = this.props

    const headerFormat = value => <Table.Heading>{value}</Table.Heading>
    const cellFormat = (value) => (
      <Table.Cell>{value}</Table.Cell>)
    const cellJobFormat = (value) => (
      <Table.Cell>
        <Link to={this.props.tenant.linkPrefix + '/job/' + value}>
          {value}
        </Link>
      </Table.Cell>)
    const cellBuildFormat = (value) => (
      <Table.Cell>
        <Link to={this.props.tenant.linkPrefix + '/builds?job_name=' + value}>
          builds
        </Link>
      </Table.Cell>)
    const columns = []
    const myColumns = ['name', 'description', 'Last builds']
    myColumns.forEach(column => {
      let formatter = cellFormat
      let prop = column
      if (column === 'name') {
        formatter = cellJobFormat
      }
      if (column === 'Last builds') {
        prop = 'name'
        formatter = cellBuildFormat
      }
      columns.push({
        header: {label: column,
          formatters: [headerFormat]},
        property: prop,
        cell: {formatters: [formatter]}
      })
    })
    return (
      <Table.PfProvider
        striped
        bordered
        hover
        columns={columns}
        >
        <Table.Header/>
        <Table.Body
          rows={jobs}
          rowKey="name"
          />
      </Table.PfProvider>
    )
  }
}

export default connect(state => ({
  tenant: state.tenant,
}))(JobsList)

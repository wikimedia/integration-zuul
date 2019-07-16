:title: Tenant Scoped REST API

Tenant Scoped REST API
======================

Users can perform some privileged actions at the tenant level through protected
endpoints of the REST API, if these endpoints are activated.

The supported actions are **autohold**, **enqueue/enqueue-ref** and
**dequeue/dequeue-ref**. These are similar to the ones available through Zuul's
CLI.

The protected endpoints require a bearer token, passed to Zuul Web Server as the
**Authorization** header of the request. The token and this workflow follow the
JWT standard as established in this `RFC <https://tools.ietf.org/html/rfc7519>`_.

Important Security Considerations
---------------------------------

Anybody with a valid Token can perform privileged actions exposed
through the REST API. Furthermore revoking Tokens, especially when manually
issued, is not trivial.

As a mitigation, Tokens should be generated with a short time to
live, like 10 minutes or less. If the Token contains authorization Information
(see the ``zuul.admin`` claim below), it should be generated with as little a scope
as possible (one tenant only) to reduce the surface of attack should the
Token be compromised.

Exposing administration tasks can impact build results (dequeue-ing buildsets),
and pose potential resources problems with Nodepool if the ``autohold`` feature
is abused, leading to a significant number of nodes remaining in "hold" state for
extended periods of time. As always, "with great power comes great responsibility"
and Tokens should be handed over with discernment.

Configuration
-------------

See the Zuul Web Server component's section about enabling tenant-scoped access to
privileged actions.

JWT Format
----------

Zuul can consume JWTs with the following minimal set of claims:

.. code-block:: javascript

  {
   'iss': 'jwt_provider',
   'aud': 'my_zuul_deployment',
   'exp': 1234567890,
   'iat': 1234556780,
   'sub': 'venkman'
  }

* **iss** is the issuer of the Token. It can be used to filter
  Identity Providers.
* **aud**, as the intended audience, is usually the client id as registered on
  the Identity Provider.
* **exp** is the Token's expiry timestamp.
* **iat** is the Token's date of issuance timestamp.
* **sub** is the default, unique identifier of the user.

JWTs can be extended arbitrarily with other claims. Zuul however can look for a
specific **zuul** claim, if the ``allow_authz_override`` option was set to True
in the authenticator's configuration. This claim has the following format:

.. code-block:: javascript

  {
   'zuul': {
      'admin': ['tenant-one', 'tenant-two']
    }
  }

The **admin** field is a list of tenants on which the Token's bearer is granted
the right to perform privileged actions.

Manually Generating a JWT
-------------------------

An operator can generate a JWT by using the settings of a configured authenticator
in ``zuul.conf``.

For example, in Python, and for an authenticator using the ``HS256`` algorithm:

.. code-block:: python

   >>> import jwt
   >>> import time
   >>> jwt.encode({'sub': 'user1',
                   'iss': <issuer_id>,
                   'aud': <client_id>,
                   'iat': time.time(),
                   'exp': time.time() + 300,
                   'zuul': {
                            'admin': ['tenant-one']
                           }
                  }, <secret>, algorithm='HS256')
   'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ6dXVsIjp7ImFkbWluIjpbInRlbmFudC1vbmUiXX0sInN1YiI6InZlbmttYW4iLCJpc3MiOiJtYW51YWwiLCJleHAiOjE1NjAzNTQxOTcuMTg5NzIyLCJpYXQiOjE1NjAzNTM4OTcuMTg5NzIxLCJhdWQiOiJ6dXVsIn0.Qqb-ANmYv8slNUVSqjCJDL8HlH9L7nnLtLU2HBGzQJk'

Online resources like https://jwt.io are also available to generate, decode and
debug JWTs.

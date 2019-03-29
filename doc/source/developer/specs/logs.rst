Web Dashboard Log Handling
==========================

.. warning:: This is not authoritative documentation.  These features
   are not currently available in Zuul.  They may change significantly
   before final implementation, or may never be fully completed.

The OpenStack project infrastructure developed some useful log hosting
tools and practices, but they are neither scalable nor generally
applicable to other projects.  This spec describes some of those
features and how we can incorporate them into Zuul in a more widely
applicable way.

In general, OpenStack uses a static log server and several features of
Apache, including a python WSGI app, to mediate access to the logs.
This provides directory browsing, log severity filters and highlights,
deep linking to log lines, and dynamic rendering of ARA from sqlite
databases.

We can expand the role of the Zuul dashboard to take on some of those
duties, thereby reducing the storage requirements, and offloading the
computation to the browser of the user requesting it.  In the process,
we can make a better experience for users navigating log files, all
without compromising Zuul's independence from backend storage.

All of what follows should apply equally to file or swift-based
storage.

Much of this functionality centers on enhancements to the existing
per-build page in the web dashboard.

If we have the uploader also create and upload a file manifest (say,
zuul-manifest.json), then the build page can fetch [#f1]_ this file
from the log storage and display an index of artifacts for the build.
This can allow for access to artifacts directly within the Zuul web
user interface, without the discontinuity of hitting an artifact
server for the index.  Since swift (and other object storage systems)
may not be capable of dynamically creating indexes, this allows the
functionality to work in all cases and removes the need to
pre-generate index pages when using swift.

We have extended Zuul to store additional artifact information about a
build.  For example, in our docs build jobs, we override the
success-url to link to the generated doc content.  If we return the
preview location as additional information about the build, we can
highlight that in a prominent place on the build page.  In the future,
we may want to leave the success-url alone so that users visit the
build page and then follow a link to the preview.  It would be an
extra click compared to what we do today, but it may be a better
experience in the long run.  Either way would still be an option, of
course, according to operator preference.

Another important feature we currently have in OpenStack is a piece of
middleware we call "os-loganalyze" (or OSLA) which HTMLifies text
logs.  It dynamically parses log files and creates anchors for each
line (for easy hyperlink references) and also highlights and allows
filtering by severity.

We could implement this in javascript, so that when a user browses
from the build page to a text file, the javascript component could
fetch [#f1]_ the file and perform the HTMLification as OSLA does
today.  If it can't parse the file, it will just pass it through
unchanged.  And in the virtual file listing on the build page, we can
add a "raw" link for direct download of the file without going through
the HTMLification.  This would eliminate the need for us to pre-render
content when we upload to swift, and by implementing this in a generic
manner in javascript in Zuul, more non-OpenStack Zuul users would
benefit from this functionality.

Any unknown files, or existing .html files, should just be direct links
which leave the Zuul dashboard interface (we don't want to attempt to
proxy or embed everything).

Finally, even today Zuul creates a job-output.json, with the idea that
we would eventually use it as a substitute for job-output.txt when
reviewing logs.  The build page would be a natural launching point for a
page which read this json file and created a more interactive and
detailed version of the streaming job output.

In summary, by moving some of the OpenStack-specific log processing into
Zuul-dashboard javascript, we can make it more generic and widely
applicable, and at the same time provide better support for multiple log
storage systems.

.. [#f1] Swift does support specifying CORS headers.  Users would need
         to do so to support this, and users of static file servers would need
         to do something similar.

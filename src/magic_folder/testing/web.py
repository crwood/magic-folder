# -*- coding: utf-8 -*-
# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2020 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

"""
Test-helpers for clients that use the WebUI.

NOTE: This code should be in upstream Tahoe-LAFS.  None of it exists in
1.14.0.  Some of it has been pushed upstream and will make it into 1.15.0
without further efforts but other parts have not.  Changes here should always
be pushed upstream eventually but not so quickly that we have to submit a PR
to Tahoe-LAFS every few days.
"""

from functools import (
    partial,
)

import hashlib

import attr

from hyperlink import DecodedURL

from twisted.web.resource import (
    Resource,
)
from twisted.web.iweb import (
    IBodyProducer,
)
from twisted.web import (
    http,
)

from twisted.internet.defer import (
    succeed,
)

from treq.client import (
    HTTPClient,
    FileBodyProducer,
)
from treq.testing import (
    RequestTraversalAgent,
)
from zope.interface import implementer

import allmydata.uri
from allmydata.util import (
    base32,
)


__all__ = (
    "create_fake_tahoe_root",
    "create_tahoe_treq_client",
)


class _FakeTahoeRoot(Resource, object):
    """
    An in-memory 'fake' of a Tahoe WebUI root. Currently it only
    implements (some of) the `/uri` resource.
    """

    def __init__(self, uri=None):
        """
        :param uri: a Resource to handle the `/uri` tree.
        """
        Resource.__init__(self)  # this is an old-style class :(
        self._uri = uri
        self.putChild(b"uri", self._uri)

    def add_data(self, kind, data):
        return self._uri.add_data(kind, data)

    def add_mutable_data(self, kind, data):
        # Adding mutable data always makes a new object.
        return self._uri.add_mutable_data(kind, data)


KNOWN_CAPABILITIES = [
    getattr(allmydata.uri, t).BASE_STRING
    for t in dir(allmydata.uri)
    if hasattr(getattr(allmydata.uri, t), 'BASE_STRING')
]


def capability_generator(kind):
    """
    Deterministically generates a stream of valid capabilities of the
    given kind. The N, K and size values aren't related to anything
    real.

    :param str kind: the kind of capability, like `URI:CHK`

    :returns: a generator that yields new capablities of a particular
        kind.
    """
    if kind not in KNOWN_CAPABILITIES:
        raise ValueError(
            "Unknown capability kind '{} (valid are {})'".format(
                kind,
                ", ".join(KNOWN_CAPABILITIES),
            )
        )
    # what we do here is to start with empty hashers for the key and
    # ueb_hash and repeatedly feed() them a zero byte on each
    # iteration .. so the same sequence of capabilities will always be
    # produced. We could add a seed= argument if we wanted to produce
    # different sequences.
    number = 0
    key_hasher = hashlib.new("sha256")
    ueb_hasher = hashlib.new("sha256")  # ueb means "URI Extension Block"

    # capabilities are "prefix:<128-bits-base32>:<256-bits-base32>:N:K:size"
    while True:
        number += 1
        key_hasher.update("\x00")
        ueb_hasher.update("\x00")

        key = base32.b2a(key_hasher.digest()[:16])  # key is 16 bytes
        ueb_hash = base32.b2a(ueb_hasher.digest())  # ueb hash is 32 bytes

        cap = u"{kind}{key}:{ueb_hash}:{n}:{k}:{size}".format(
            kind=kind,
            key=key,
            ueb_hash=ueb_hash,
            n=1,
            k=1,
            size=number * 1000,
        )
        yield cap.encode("ascii")


@attr.s
class _FakeTahoeUriHandler(Resource, object):
    """
    An in-memory fake of (some of) the `/uri` endpoint of a Tahoe
    WebUI
    """

    isLeaf = True

    data = attr.ib(default=attr.Factory(dict))
    capability_generators = attr.ib(default=attr.Factory(dict))

    def _generate_capability(self, kind):
        """
        :param str kind: any valid capability-string type

        :returns: the next capability-string for the given kind
        """
        if kind not in self.capability_generators:
            self.capability_generators[kind] = capability_generator(kind)
        capability = next(self.capability_generators[kind])
        return capability

    def _add_new_data(self, kind, data):
        """
        Add brand new data to the store.

        :param bytes kind: The kind of capability, represented as the static
            string prefix on the resulting capability string (eg "URI:DIR2:").

        :param data: The data.  The type varies depending on ``kind``.

        :return bytes: The capability-string for the data.
        """
        cap = self._generate_capability(kind)
        # it should be impossible for this to already be in our data,
        # but check anyway to be sure
        if cap in self.data:
            raise Exception("Internal error; key already exists somehow")
        self.data[cap] = data
        return cap

    def add_data(self, kind, data):
        """
        Add some immutable data to our grid.

        If the data exists already, an existing capability is returned.
        Otherwise, a new capability is returned.

        :return (bool, bytes): The first element is True if the data is
            freshly added.  The second element is the capability-string for
            the data.
        """
        if not isinstance(data, bytes):
            raise TypeError("'data' must be bytes")

        for k in self.data:
            if self.data[k] == data:
                return (False, k)

        return (True, self._add_new_data(kind, data))

    def add_mutable_data(self, kind, data):
        """
        Add some mutable data to our grid.

        :return bytes: The capability-string for the data.
        """
        if not isinstance(data, bytes):
            raise TypeError("'data' must be bytes")
        return (False, self._add_new_data(kind, data))

    def render_PUT(self, request):
        uri = DecodedURL.from_text(request.uri.decode("utf8"))
        fmt = "chk"
        for arg, value in uri.query:
            if arg == "format":
                fmt = value.lower()
        if fmt != "chk":
            raise NotImplementedError()

        data = request.content.read()
        fresh, cap = self.add_data("URI:CHK:", data)
        if fresh:
            request.setResponseCode(http.CREATED)  # real code does this for brand-new files
        else:
            request.setResponseCode(http.OK)  # replaced/modified files
        return cap

    def render_POST(self, request):
        t = request.args[u"t"][0]
        data = request.content.read()

        type_to_handler = {
            "mkdir-immutable": partial(self.add_data, "URI:DIR2-CHK:"),
            "mkdir": partial(self.add_mutable_data, "URI:DIR2:"),
        }
        handler = type_to_handler[t]
        fresh, cap = handler(data)
        return cap

    def render_GET(self, request):
        uri = DecodedURL.from_text(request.uri.decode('utf8'))
        capability = None
        for arg, value in uri.query:
            if arg == u"uri":
                capability = value
        # it's legal to use the form "/uri/<capability>"
        if capability is None and request.postpath and request.postpath[0]:
            capability = request.postpath[0]

        # if we don't yet have a capability, that's an error
        if capability is None:
            request.setResponseCode(http.BAD_REQUEST)
            return b"GET /uri requires uri="

        # the user gave us a capability; if our Grid doesn't have any
        # data for it, that's an error.
        if capability not in self.data:
            # Tahoe-LAFS actually has several different behaviors for the
            # ostensible "not found" case.
            #
            # * A request for a CHK cap will receive a GONE response with
            #   "NoSharesError" (and some other text) in a text/plain body.
            # * A request for a DIR2 cap will receive an OK response with
            #   a huge text/html body including "UnrecoverableFileError".
            # * A request for the child of a DIR2 cap will receive a GONE
            #   response with "UnrecoverableFileError" (and some other text)
            #   in a text/plain body.
            #
            # Also, all of these are *actually* behind a redirect to
            # /uri/<CAP>.
            #
            # GONE makes the most sense here and I don't want to deal with
            # redirects so here we go.
            request.setResponseCode(http.GONE)
            return u"No data for '{}'".format(capability).encode("ascii")

        return self.data[capability]


def create_fake_tahoe_root():
    """
    If you wish to pre-populate data into the fake Tahoe grid, retain
    a reference to this root by creating it yourself and passing it to
    `create_tahoe_treq_client`. For example::

        root = create_fake_tahoe_root()
        cap_string = root.add_data(...)
        client = create_tahoe_treq_client(root)

    :returns: an IResource instance that will handle certain Tahoe URI
        endpoints similar to a real Tahoe server.
    """
    root = _FakeTahoeRoot(
        uri=_FakeTahoeUriHandler(),
    )
    return root


@implementer(IBodyProducer)
class _SynchronousProducer(object):
    """
    A partial implementation of an :obj:`IBodyProducer` which produces its
    entire payload immediately.  There is no way to access to an instance of
    this object from :obj:`RequestTraversalAgent` or :obj:`StubTreq`, or even a
    :obj:`Resource: passed to :obj:`StubTreq`.

    This does not implement the :func:`IBodyProducer.stopProducing` method,
    because that is very difficult to trigger.  (The request from
    `RequestTraversalAgent` would have to be canceled while it is still in the
    transmitting state), and the intent is to use `RequestTraversalAgent` to
    make synchronous requests.
    """

    def __init__(self, body):
        """
        Create a synchronous producer with some bytes.
        """
        if isinstance(body, FileBodyProducer):
            body = body._inputFile.read()

        if not isinstance(body, bytes):
            raise ValueError(
                "'body' must be bytes not '{}'".format(type(body))
            )
        self.body = body
        self.length = len(body)

    def startProducing(self, consumer):
        """
        Immediately produce all data.
        """
        consumer.write(self.body)
        return succeed(None)


def create_tahoe_treq_client(root=None):
    """
    :param root: an instance created via `create_fake_tahoe_root`. The
        caller might want a copy of this to call `.add_data` for example.

    :returns: an instance of treq.client.HTTPClient wired up to
        in-memory fakes of the Tahoe WebUI. Only a subset of the real
        WebUI is available.
    """

    if root is None:
        root = create_fake_tahoe_root()

    client = HTTPClient(
        agent=RequestTraversalAgent(root),
        data_to_body_producer=_SynchronousProducer,
    )
    return client
"""
Test invite + join workflow
"""

import sys
from functools import partial

from eliot import (
    log_message,
)
from eliot.twisted import (
    inline_callbacks,
)
import pytest
import pytest_twisted

from magic_folder.util.capabilities import (
    Capability,
)
from .util import (
    await_file_contents,
    await_file_vanishes,
    ensure_file_not_created,
    twisted_sleep,
    database_retry,
)


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_invite_join(request, reactor, temp_filepath, alice, bob, wormhole):
    """
    - alice creates a new folder
    - alice invites bob via wormhole
    - bob accepts the invite
    - we observe a file added by alice arriving at bob
    """
    magic_a = temp_filepath.child("inviter_magic")
    magic_a.makedirs()
    await alice.add("inviter", magic_a.path, scan_interval=1, poll_interval=1)

    def cleanup_alice():
        pytest_twisted.blockon(alice.leave("inviter"))
    request.addfinalizer(cleanup_alice)

    print("added alice")

    code, magic_proto, process_transport = await alice.invite("inviter", "bobby")
    print(code, magic_proto, process_transport)

    magic_b = temp_filepath.child("invited_magic")
    magic_b.makedirs()
    await bob.join(code, "invited", magic_b.path, "bob", poll_interval=1, scan_interval=1)

    def cleanup_bob():
        pytest_twisted.blockon(bob.leave("invited"))
    request.addfinalizer(cleanup_bob)

    await magic_proto.exited
    print("bob invited to alice's folder")

    # confirm that the folders are paired:

    # second, add something to bob and it should appear in alice
    content1 = b"from bobby\n" * 1000
    magic_b.child("file_from_bob").setContent(content1)

    await await_file_contents(
        magic_a.child("file_from_bob").path,
        content1,
    )

    # first, add something to alice and it should appear in bob
    content0 = b"from alice\n" * 1000
    magic_a.child("file_from_alice").setContent(content0)

    await await_file_contents(
        magic_b.child("file_from_alice").path,
        content0,
    )

from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

"""
Testing synchronizing files between participants
"""

from functools import partial
import time
from tempfile import (
    mkdtemp,
)

from eliot import Message
from twisted.python.filepath import (
    FilePath,
)
import pytest
import pytest_twisted

from magic_folder.util.capabilities import (
    to_readonly_capability,
)
from .util import (
    await_file_contents,
    ensure_file_not_created,
)


def add_snapshot(node, folder_name, path):
    """
    Take a snapshot of the given path in the given magic folder.

    :param MagigFolderEnabledNode node: The node on which to take the snapshot.
    """
    return node.add_snapshot(folder_name, path)


def periodic_scan(node, folder_name, path):
    """
    Wait to given the given magic folder a change to run a periodic scan.
    This should cause the given path to be
    snapshotted.

    :param MagigFolderEnabledNode node: The node on which to do the scan.
    """
    Message.log(message_type="integration:wait_for_scan", node=node.name, folder=folder_name)
    time.sleep(1)

@pytest.fixture(name='periodic_scan')
def enable_periodic_scans(magic_folder_nodes, monkeypatch):
    """
    A fixture causes magic folders to have periodic scans enabled (with
    interval of 1s), and returns a function to take a snapshot of a file (that
    waits for the scanner to run).
    """
    for node in magic_folder_nodes.values():
        monkeypatch.setattr(node, "add", partial(node.add, scan_interval=1))
    return periodic_scan


@pytest.fixture(
    params=[
        add_snapshot,
        pytest.lazy_fixture('periodic_scan'),
    ]
)
def take_snapshot(request, magic_folder_nodes):
    """
    Pytest fixture that parametrizes different ways of having
    magic-folder take a local snapshot of a given file.

    - use the `POST /v1/magic-folder/<folder-name>/snapshot` endpoint
      to request the snapshot directly.
    - use the `PUT /v1/magic-folder/<folder-name>/scan` endpoint to request
      a scan, which will cause a snapshot to be taken.

    :returns Callable[[MagicFolderEnabledNode, unicode, unicode], Deferred[None]]:
        A callable that takes a node, folder name, and relative path to a file
        that should be snapshotted.
    """
    return request.param


@pytest_twisted.inlineCallbacks
def test_local_snapshots(request, reactor, temp_dir, alice, bob, take_snapshot):
    """
    Create several snapshots while our Tahoe client is offline.
    """

    magic = FilePath(mkdtemp())

    # add our magic-folder and re-start
    yield alice.add("local", magic.path)
    local_cfg = alice.global_config().get_magic_folder("local")

    def cleanup():
        pytest_twisted.blockon(alice.leave("local"))
    request.addfinalizer(cleanup)

    # put a file in our folder
    content0 = "zero\n" * 1000
    magic.child("sylvester").setContent(content0)
    yield take_snapshot(alice, "local", "sylvester")

    # wait until we've definitely uploaded it
    for _ in range(10):
        time.sleep(1)
        try:
            former_remote = local_cfg.get_remotesnapshot("sylvester")
            break
        except KeyError:
            pass
    x = yield alice.dump_state("local")
    print(x)

    # turn off Tahoe
    alice.pause_tahoe()

    try:
        # add several snapshots
        content1 = "one\n" * 1000
        magic.child("sylvester").setContent(content1)
        yield take_snapshot(alice, "local", "sylvester")
        content2 = "two\n" * 1000
        magic.child("sylvester").setContent(content2)
        yield take_snapshot(alice, "local", "sylvester")
        content3 = "three\n" * 1000
        magic.child("sylvester").setContent(content3)
        yield take_snapshot(alice, "local", "sylvester")

        x = yield alice.dump_state("local")
        print(x)

        assert local_cfg.get_all_localsnapshot_paths() == {"sylvester"}
        snap = local_cfg.get_local_snapshot("sylvester")
        print(snap)
        # we should have 3 snapshots total, each one the parent of the next
        assert len(snap.parents_local) == 1 and \
            len(snap.parents_local[0].parents_local) == 1 and \
            len(snap.parents_local[0].parents_local[0].parents_local) == 0 and \
            len(snap.parents_local[0].parents_local[0].parents_remote) == 1

    finally:
        # turn Tahoe back on
        alice.resume_tahoe()

    # local snapshots should turn into remotes...and thus change our
    # remote snapshot pointer
    found = False
    for _ in range(10):
        if len(local_cfg.get_all_localsnapshot_paths()) == 0:
            if local_cfg.get_remotesnapshot("sylvester") != former_remote:
                found = True
                break
        time.sleep(1)
    assert found, "Expected 'sylvester' to be (only) a remote-snapshot"


@pytest_twisted.inlineCallbacks
def test_create_then_recover(request, reactor, temp_dir, alice, bob, take_snapshot):
    """
    Test a version of the expected 'recover' workflow:
    - make a magic-folder on device 'alice'
    - add a file
    - create a Snapshot for the file
    - change the file
    - create another Snapshot for the file

    - recovery workflow:
    - create a new magic-folder on device 'bob'
    - add the 'alice' Personal DMD as a participant
    - the latest version of the file should appear

    - bonus: the old device is found!
    - update the file in the original
    - create a Snapshot for the file (now has 3 versions)
    - the update should appear on the recovery device
    """

    # "alice" contains the 'original' magic-folder
    # "bob" contains the 'recovery' magic-folder
    magic = FilePath(mkdtemp())
    original_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("original", original_folder.path)
    alice_folders = yield alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("original"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = "zero\n" * 1000
    original_folder.child("sylvester").setContent(content0)
    yield take_snapshot(alice, "original", "sylvester")

    # update the file (so now there's two versions)
    content1 = "one\n" * 1000
    original_folder.child("sylvester").setContent(content1)
    yield take_snapshot(alice, "original", "sylvester")

    # create the 'recovery' magic-folder
    yield bob.add("recovery", recover_folder.path)

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("recovery"))
    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = to_readonly_capability(alice_folders["original"]["upload_dircap"])
    yield bob.add_participant("recovery", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content1,
        timeout=25,
    )

    # in the (ideally rare) case that the old device is found *and* a
    # new snapshot is uploaded, we put an update into the 'original'
    # folder. This also tests the normal 'update' flow as well.
    content2 = "two\n" * 1000
    original_folder.child("sylvester").setContent(content2)
    yield take_snapshot(alice, "original", "sylvester")

    # the new content should appear in the 'recovery' folder
    await_file_contents(
        recover_folder.child("sylvester").path,
        content2,
    )


@pytest_twisted.inlineCallbacks
def test_internal_inconsistency(request, reactor, temp_dir, alice, bob, take_snapshot):
    # FIXME needs docstring
    magic = FilePath(mkdtemp())
    original_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("internal", original_folder.path)
    alice_folders = yield alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("internal"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = "zero\n" * 1000
    original_folder.child("sylvester").setContent(content0)
    yield take_snapshot(alice, "internal", "sylvester")

    # create the 'rec' magic-folder
    yield bob.add("rec", recover_folder.path)

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("rec"))
    request.addfinalizer(cleanup_recovery)

    # add the 'internal' magic-folder as a participant in the
    # 'rec' folder
    alice_cap = to_readonly_capability(alice_folders["internal"]["upload_dircap"])
    yield bob.add_participant("rec", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content0,
        timeout=25,
    )

    yield bob.stop_magic_folder()

    # update the file (so now there's two versions)
    content1 = "one\n" * 1000
    original_folder.child("sylvester").setContent(content1)
    yield take_snapshot(alice, "internal", "sylvester")

    time.sleep(2)

    yield bob.start_magic_folder()

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content1,
        timeout=25,
    )


@pytest_twisted.inlineCallbacks
def test_ancestors(request, reactor, temp_dir, alice, bob, take_snapshot):
    magic = FilePath(mkdtemp())
    original_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("ancestor0", original_folder.path)
    alice_folders = yield alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("ancestor0"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = "zero\n" * 1000
    original_folder.child("sylvester").setContent(content0)
    yield take_snapshot(alice, "ancestor0", "sylvester")

    # create the 'ancestor1' magic-folder
    yield bob.add("ancestor1", recover_folder.path)

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("ancestor1"))
    request.addfinalizer(cleanup_recovery)

    # add the 'ancestor0' magic-folder as a participant in the
    # 'ancestor1' folder
    alice_cap = to_readonly_capability(alice_folders["ancestor0"]["upload_dircap"])
    yield bob.add_participant("ancestor1", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'ancestor1' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content0,
        timeout=25,
    )

    # update the file in bob's folder
    content1 = "one\n" * 1000
    recover_folder.child("sylvester").setContent(content1)
    yield take_snapshot(bob, "ancestor1", "sylvester")

    await_file_contents(
        recover_folder.child("sylvester").path,
        content1,
        timeout=25,
    )
    ensure_file_not_created(
        recover_folder.child("sylvester.conflict-alice").path,
        timeout=25,
    )

    # update the file in alice's folder
    content2 = "two\n" * 1000
    original_folder.child("sylvester").setContent(content2)
    yield take_snapshot(alice, "ancestor0", "sylvester")

    # Since we made local changes to the file, a change to alice
    # shouldn't overwrite our changes
    await_file_contents(
        recover_folder.child("sylvester").path,
        content1,
        timeout=25,
    )

@pytest_twisted.inlineCallbacks
def test_recover_twice(request, reactor, temp_dir, alice, bob, edmond, take_snapshot):
    magic = FilePath(mkdtemp())
    original_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    recover2_folder = magic.child("mice")
    original_folder.makedirs()
    recover_folder.makedirs()
    recover2_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("original", original_folder.path)
    alice_folders = yield alice.list_(True)

    def cleanup_original():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(alice.start_magic_folder())
        pytest_twisted.blockon(alice.leave("original"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = "zero\n" * 1000
    original_folder.child("sylvester").setContent(content0)
    yield take_snapshot(alice, "original", "sylvester")

    time.sleep(5)
    yield alice.stop_magic_folder()

    # create the 'recovery' magic-folder
    yield bob.add("recovery", recover_folder.path)
    bob_folders = yield bob.list_(True)

    def cleanup_recovery():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(bob.start_magic_folder())
        pytest_twisted.blockon(bob.leave("recovery"))
    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = to_readonly_capability(alice_folders["original"]["upload_dircap"])
    yield bob.add_participant("recovery", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content0,
        timeout=25,
    )

    # update the file (so now there's two versions)
    content1 = "one\n" * 1000
    recover_folder.child("sylvester").setContent(content1)
    yield take_snapshot(bob, "recovery", "sylvester")

    # We shouldn't see this show up as a conflict, since we are newer than
    # alice
    ensure_file_not_created(
        recover_folder.child("sylvester.conflict-alice").path,
        timeout=25,
    )

    time.sleep(5)
    yield bob.stop_magic_folder()

    # create the second 'recovery' magic-folder
    yield edmond.add("recovery-2", recover2_folder.path)

    def cleanup_recovery_2():
        pytest_twisted.blockon(edmond.leave("recovery-2"))
    request.addfinalizer(cleanup_recovery_2)

    # add the 'recovery' magic-folder as a participant in the
    # 'recovery-2' folder
    bob_cap = to_readonly_capability(bob_folders["recovery"]["upload_dircap"])
    yield edmond.add_participant("recovery-2", "bob", bob_cap)

    await_file_contents(
        recover2_folder.child("sylvester").path,
        content1,
        timeout=25,
    )
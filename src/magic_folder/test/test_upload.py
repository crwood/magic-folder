import io

import attr

from zope.interface import (
    implementer,
)

from testtools.matchers import (
    MatchesPredicate,
    Always,
    Equals,
)
from testtools import (
    ExpectedException,
)
from testtools.twistedsupport import (
    succeeded,
)
from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
    lists,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.web.resource import (
    ErrorPage,
)
from hyperlink import (
    DecodedURL,
)
from ..magic_folder import (
    IRemoteSnapshotCreator,
    UploaderService,
    RemoteSnapshotCreator,
)
from ..config import (
    SQLite3DatabaseLocation,
    MagicFolderConfig,
    SnapshotNotFound,
)
from ..snapshot import (
    create_local_author,
    create_snapshot,
)
from twisted.internet import task

from .common import (
    SyncTestCase,
)
from .strategies import (
    path_segments,
)

from magic_folder.testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from magic_folder.tahoe_client import (
    TahoeAPIError,
    create_tahoe_client,
)
from allmydata.uri import is_uri

from fixtures import (
    Fixture,
)

class RemoteSnapshotCreatorFixture(Fixture):
    """
    A fixture which provides a ``RemoteSnapshotCreator`` connected to a
    ``MagicFolderConfig``.
    """
    def __init__(self, temp, author, root=None):
        """
        :param FilePath temp: A path where the fixture may write whatever it
            likes.

        :param LocalAuthor author: The author which will be used to sign
            snapshots the ``RemoteSnapshotCreator`` creates.

        :param IResource root: The root resource for the fake Tahoe-LAFS HTTP
            API hierarchy.  The default is one created by
            ``create_fake_tahoe_root``.
        """
        if root is None:
            root = create_fake_tahoe_root()
        self.temp = temp
        self.author = author
        self.root = root
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.com"),
            self.http_client,
        )

    def _setUp(self):
        self.magic_path = self.temp.child(b"magic")
        self.magic_path.makedirs()

        self.stash_path = self.temp.child(b"stash")
        self.stash_path.makedirs()

        self.poll_interval = 1

        self.state_db = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash_path,
            u"URI:DIR2-RO:aaa:bbb",
            u"URI:DIR2:ccc:ddd",
            self.magic_path,
            self.poll_interval,
        )

        self.remote_snapshot_creator = RemoteSnapshotCreator(
            state_db=self.state_db,
            local_author=self.author,
            tahoe_client=self.tahoe_client,
        )


class RemoteSnapshotCreatorTests(SyncTestCase):
    """
    Tests for ``RemoteSnapshotCreator``.
    """
    def setUp(self):
        super(RemoteSnapshotCreatorTests, self).setUp()
        self.author = create_local_author("alice")

    @given(name=path_segments(),
           content=binary(),
    )
    def test_commit_a_file(self, name, content):
        """
        Add a file into localsnapshot store, start the service which
        should result in a remotesnapshot corresponding to the
        localsnapshot.
        """
        f = self.useFixture(RemoteSnapshotCreatorFixture(
            temp=FilePath(self.mktemp()),
            author=self.author,
        ))
        state_db = f.state_db
        remote_snapshot_creator = f.remote_snapshot_creator

        # create a local snapshot
        data = io.BytesIO(content)

        d = create_snapshot(
            name=name,
            author=self.author,
            data_producer=data,
            snapshot_stash_dir=state_db.stash_path,
            parents=[],
        )

        snapshots = []
        d.addCallback(snapshots.append)

        self.assertThat(
            d,
            succeeded(Always()),
        )

        # push LocalSnapshot object into the SnapshotStore.
        # This should be picked up by the Uploader Service and should
        # result in a snapshot cap.
        state_db.store_local_snapshot(snapshots[0])

        d = remote_snapshot_creator.upload_local_snapshots()
        self.assertThat(
            d,
            succeeded(Always()),
        )

        remote_snapshot_cap = state_db.get_remotesnapshot(name)

        # test whether we got a capability
        self.assertThat(
            remote_snapshot_cap,
            MatchesPredicate(is_uri,
                             "%r is not a Tahoe-LAFS URI"),
        )

        with ExpectedException(SnapshotNotFound, ""):
            state_db.get_local_snapshot(name, self.author)

    @given(
        path_segments(),
        lists(
            binary(),
            min_size=1,
            max_size=2,
        ),
    )
    def test_write_snapshot_to_tahoe_fails(self, name, contents):
        """
        If any part of a snapshot upload fails then the metadata for that snapshot
        is retained in the local database and the snapshot content is retained
        in the stash.
        """
        broken_root = ErrorPage(500, "It's broken.", "It's broken.")

        f = self.useFixture(RemoteSnapshotCreatorFixture(
            temp=FilePath(self.mktemp()),
            author=self.author,
            root=broken_root,
        ))
        state_db = f.state_db
        remote_snapshot_creator = f.remote_snapshot_creator

        snapshots = []
        parents = []
        for content in contents:
            data = io.BytesIO(content)
            d = create_snapshot(
                name=name,
                author=self.author,
                data_producer=data,
                snapshot_stash_dir=state_db.stash_path,
                parents=parents,
            )
            d.addCallback(snapshots.append)
            self.assertThat(
                d,
                succeeded(Always()),
            )
            parents = [snapshots[-1]]

        local_snapshot = snapshots[-1]
        state_db.store_local_snapshot(snapshots[-1])

        d = remote_snapshot_creator.upload_local_snapshots()
        self.assertThat(
            d,
            succeeded(Always()),
        )

        self.eliot_logger.flushTracebacks(TahoeAPIError)

        self.assertEqual(
            local_snapshot,
            state_db.get_local_snapshot(name, self.author),
        )
        self.assertThat(
            local_snapshot.content_path.getContent(),
            Equals(content),
        )


@implementer(IRemoteSnapshotCreator)
@attr.s
class MemorySnapshotCreator(object):
    _uploaded = attr.ib(default=0)

    def upload_local_snapshots(self):
        self._uploaded += 1


class UploaderServiceTests(SyncTestCase):
    """
    Tests for ``UploaderService``.
    """
    def setUp(self):
        super(UploaderServiceTests, self).setUp()
        self.poll_interval = 1
        self.clock = task.Clock()
        self.remote_snapshot_creator = MemorySnapshotCreator()
        self.uploader_service = UploaderService(
            poll_interval=self.poll_interval,
            clock=self.clock,
            remote_snapshot_creator=self.remote_snapshot_creator,
        )

    def test_commit_a_file(self):
        # start Uploader Service
        self.uploader_service.startService()
        self.addCleanup(self.uploader_service.stopService)

        # We want processing to start immediately on startup in case there was
        # work left over from the last time we ran.  So there should already
        # have been one upload attempt by now.
        self.assertThat(
            self.remote_snapshot_creator._uploaded,
            Equals(1),
        )

        # advance the clock manually, which should result in the
        # polling of the db for uncommitted LocalSnapshots in the db
        # and then check for remote snapshots
        self.clock.advance(self.poll_interval)

        self.assertThat(
            self.remote_snapshot_creator._uploaded,
            Equals(2),
        )

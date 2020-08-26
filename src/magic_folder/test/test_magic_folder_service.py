# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for the Twisted service which is responsible for a single
magic-folder.
"""

from __future__ import (
    absolute_import,
    print_function,
    division,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.application.service import (
    Service,
)
from hypothesis import (
    given
)
from hypothesis.strategies import (
    binary,
)
from testtools.matchers import (
    Is,
    Always,
    Equals,
)
from testtools.twistedsupport import (
    succeeded,
)
from ..magic_folder import (
    MagicFolder,
    LocalSnapshotService,
)

from .common import (
    SyncTestCase,
)
from .strategies import (
    relative_paths,
)
from .test_local_snapshot import (
    MemorySnapshotCreator as LocalMemorySnapshotCreator,
)

class MagicFolderServiceTests(SyncTestCase):
    """
    Tests for ``MagicFolder``.
    """
    def test_local_snapshot_service_child(self):
        """
        ``MagicFolder`` adds the service given as ``LocalSnapshotService`` to
        itself as a child.
        """
        local_snapshot_service = Service()
        tahoe_client = object()
        reactor = object()
        name = u"local-snapshot-service-test"
        config = object()
        participants = object()
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            uploader_service=Service(),
            initial_participants=participants,
            clock=reactor,
        )
        self.assertThat(
            local_snapshot_service.parent,
            Is(magic_folder),
        )

    @given(
        relative_target_path=relative_paths(),
        content=binary(),
    )
    def test_create_local_snapshot(self, relative_target_path, content):
        """
        ``MagicFolder.local_snapshot_service`` can be used to create a new local
        snapshot for a file in the folder.
        """
        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        target_path = magic_path.preauthChild(relative_target_path).asBytesMode("utf-8")
        target_path.parent().makedirs(ignoreExistingDirectory=True)
        target_path.setContent(content)

        local_snapshot_creator = LocalMemorySnapshotCreator()
        local_snapshot_service = LocalSnapshotService(magic_path, local_snapshot_creator)
        clock = object()

        tahoe_client = object()
        name = u"local-snapshot-service-test"
        config = object()
        participants = object()
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            uploader_service=Service(),
            initial_participants=participants,
            clock=clock,
        )
        magic_folder.startService()
        self.addCleanup(magic_folder.stopService)

        adding = magic_folder.local_snapshot_service.add_file(
            target_path,
        )
        self.assertThat(
            adding,
            succeeded(Always()),
        )

        self.assertThat(
            local_snapshot_creator.processed,
            Equals([target_path]),
        )

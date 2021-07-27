# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder join command.
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
)


def magic_folder_join(config, invite_code, local_dir, name, poll_interval, author_name):
    """
    Join a magic-folder specified by the ``name`` and create the config files.

    :param GlobalConfigDatabase config: our configuration

    :param unicode invite_code: The code used to join a magic folder.

    :param FilePath local_dir: The directory in the local filesystem that holds
        the files to be synchronized across computers.

    :param unicode name: The magic-folder name.

    :param integer poll_interval: Periodic time interval after which the
        client polls for updates.

    :param unicode author_name: Our own name for Snapshot authorship

    :return: None or exception is raised on error.
    """
    # to join a folder we need:
    # - collective read-cap
    # - create our own write-cap
    # - turn ^ into a read-cap
    # - cause ^ to get added to the Collective

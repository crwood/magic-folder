
.. -*- coding: utf-8 -*-

.. _downloader:

Downloader Operation
====================

This describes the general operation of the remote to local
synchronization as outlined in "the Leif Design" (see :ref:`Leif's
Proposal: Magic-Folder "single-file" snapshot design`).

We do not describe the synchronization of the list of participants in
a magic-folder. This list will consist of at least:

- an arbitrary ``name`` for each participant
- a directory read-capability for each participant ("personal DMD")
  - in this directory is a flattened representation of every file in
    the Magic Folder (similar to the Tahoe 1.14.0 design) with each
    entry pointing to a Snapshot. That is, there are no
    sub-directories to recurse into, all files are listed by
    downloading just the "personal DMD" capability of that participant.

"A Snapshot" is a single version of a single file. It is represented
by an immutable directory and contains:

- ``content``: (optional) a read-only link to the actual content of
  this Snapshot. If there is no such link, this is a deletion
  snapshot.
- ``metadata``: information about the Snapshot, a capability pointing
  to a JSON-serialized dict containing:
  - ``snapshot_version``: 1 currently
  - ``name``: the name of this snapshot (a mangled relative path)
  - ``author``: a dict containing:
    - ``name``: arbitrary name
    - ``verify_key``: base64-encoded public key of the author
  - ``parents``: a list of immutable capability-strings of any parent Snapshots
  - additionally, in the Tahoe metadata for this metadata-capability
    is a ``magic_folder`` dict with the following keys:
    - ``author_signature``: base64-encoded signature which signs the
      content-capability, metadata-capability and name

If there are zero parents, this is the first version of a
file. Otherwise, it is a modification. If there are two or more
parents this version is the resolution of a conflict.

When reading the code, there is a method
``create_snapshot_from_capability`` which downloads a
capability-string from Tahoe and returns a ``RemoteSnapshot`` instance
(after verifying signatures).


General Operation
-----------------

There is a service responsible for deciding which Snapshots to
download. The capability strings of snapshots to download are given to
a second service that is responsible for actually downloading
them. Downloading new Snapshots from other participants causes changes
to the local filesystem (in the magic-folder).


What Snapshots to Download
--------------------------

Each configured magic-folder has a ``collective_dircap`` which is a
Tahoe capability for the list of participants. If this dircap is
writable then this device is the administrator (and the only one who
can modify the participants).

In either case, the capability can be downloaded. It will be a Tahoe
directory containing a series of sub-directories; these are the
participants. The directory name is their name and points at a
read-capability where all the files in their magic-folder are
stored. This is known as the "Personal DMD", where DMD stands for
"Distributed Mutable Directory".

The entries in a user's Personal DMD are flat (no subdirectories) and
point a (mangled) relative path-name to a Snapshot.

So, Snapshots to download can be discovered by:

- reading the Collective DMD
- for each user in it:
  - read their Personal DMD
  - for each entry in that directory:
    - queue the Snapshot for download (unless already cached)
- any newly-downloaded Snapshot should be examined; if any of its
  parents are not cached, download them too


Downloading Snapshots
---------------------

A service awaits capability-strings of Snapshots to download. For each
one, the function ``create_snapshot_from_capability`` is used to
download the capability and return a ``RemoteSnapshot`` instance.

This ``RemoteSnapshot`` is serialized to a local cache in the
magic-folder's state database.

We also arrange to make local filesystem changes. This might require
waiting to download more ``RemoteSnapshots`` if it has any parents
that aren't cached.

Conflict Resolution is described in :ref:`Multi-party Conflict
Detection` under the Leif's Design. Briefly: a ``RemoteSnapshot`` is
traced through its parents until a common ancestor is found. If the
new Snapshot is a descendant of our latest Snapshot for that name,
it's an overwrite. If it is not, there is a conflict (unless we don't
yet have that name at all, then it's a creation).


On Overwrite
------------

The ``content`` of the ``RemoteSnapshot`` is downloaded and moved into
place in our Magic Folder. Our Personal DMD is updated to point at
this Snapshot.

In case there is no ``content`` this is a delete and we simply remove
the corresponding local file.

Note that a completely new file (a "create") is the same as a normal
overwrite (except of course there's no possibility of a conflict).


On Conflict
-----------

The ``content`` of the ``RemoteSnapshot`` is downloaded and moved into
a "conflict file" (see Leif Design) beside the conflicting
content. The Personal DMD is **not** updated. Once the conflict is
"resolved" then a new Snapshot is created with two parents: the latest
Snapshot we had at conflict time and the conflicting Snapshot. Our
Personal DMD is updated to point at this new Snapshot.

"Resolving" a snapshot is currently noticed via more filesystem
manipulation: the ``.confict`` file is deleted or moved (and the
existing file is taken to be the new content). For example, deciding
"I like the other device's file better" would mean moving the
``.conflict`` file over top of the existing one. Deciding "I like mine
better" means simply deleting the ``.conflict`` file. A more-complex
strategy of merging the contents would mean updating the existing file
**before** deleting the ``.conflict`` file.

I believe the above accurately describes what Tahoe 1.14.0
magic-folder does.

This doesn't mean it's the best "API" for conflict resolution (nor
does it need to remain the only one). We could, for example, add an
HTTP API and CLI command that explicitly say "take mine" or "take
theirs" or "take this new thing I crafted".

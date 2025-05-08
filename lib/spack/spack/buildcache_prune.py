# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import json
import pathlib
from tempfile import TemporaryFile
from typing import List, Optional, Set

import llnl.util.tty as tty

from spack.util import s3

from .mirrors.mirror import Mirror


def _direct_prune(mirror: Mirror, lockfile: str) -> None:
    """
    Deletes spec manifests that are not in the lockfile.

    Note, this does not delete the actual blobs, just the spec manifests.
    Deleting the manifests effectively orphans the blobs, which will result
    in them being deleted in the following orphan pruning step.
    """
    s3_client = s3.get_s3_session(url=mirror.fetch_url, method="fetch")
    paginator = s3_client.get_paginator("list_objects_v2")

    s3_bucket_name = mirror.fetch_url.split("/")[2]

    lockfile_json = json.loads(pathlib.Path(lockfile).read_text())

    keeplist: Set[str] = set(spec_hash for spec_hash in lockfile_json["concrete_specs"].keys())

    for page in paginator.paginate(Bucket=s3_bucket_name, Prefix="spack/v3/manifests/spec/"):
        for obj in page.get("Contents", []):
            spec_hash = obj["Key"].split("/")[-1].split(".spec.manifest.json")[0].split("-")[-1]
            if spec_hash not in keeplist:
                tty.debug(f"Found spec not in keeplist: {spec_hash}")
                # s3_client.delete_object(Bucket=bucket_name, Key=obj['Key'])
                tty.debug(f"Deleted spec manifest: {spec_hash}")


def _get_spec_checksums(s3_client, bucket_name: str, key: str) -> List[str]:
    """Get the checksum of a spec from the mirror."""
    with TemporaryFile() as tmp:
        s3_client.download_fileobj(bucket_name, key, tmp)
        tmp.seek(0)

        signed_spec_json = tmp.read().decode()
        spec_json = json.loads(
            "".join(signed_spec_json[signed_spec_json.index("{") :]).split(
                "-----BEGIN PGP SIGNATURE"
            )[0]
        )

        return [s["checksum"] for s in spec_json["data"]]


def _prune_orphaned_specs(mirror: Mirror) -> int:
    """
    Prune orphaned specs from the given mirror.

    An "orphaned spec" is defined as a spec that is not referenced by any
    manifest file in the mirror.
    """
    # The set of spec checksums that we do not want to delete
    keep_list: Set[str] = set()

    s3_client = s3.get_s3_session(url=mirror.fetch_url, method="fetch")
    paginator = s3_client.get_paginator("list_objects_v2")

    s3_bucket_name = mirror.fetch_url.split("/")[2]

    # First, build up our keep list of checksums from the manifest files
    # in the mirror.
    for page in paginator.paginate(Bucket=s3_bucket_name, Prefix="spack/v3/manifests/spec/"):
        for obj in page.get("Contents", []):
            checksums = _get_spec_checksums(s3_client, s3_bucket_name, obj["Key"])
            keep_list.update(checksums)
            for checksum in checksums:
                tty.debug(f"Added spec to keep list: {checksum}")

    # Now, we can go through the blobs and delete any that are not in the keep list.
    pruned_specs = 0
    for page in paginator.paginate(Bucket=s3_bucket_name, Prefix="spack/blobs/"):
        for obj in page.get("Contents", []):
            checksum = obj["Key"].split("/")[-1]
            if checksum not in keep_list:
                tty.debug(f"Found orphaned spec: {checksum}")
                # s3_client.delete_object(Bucket=bucket_name, Key=obj['Key'])
                tty.debug(f"Deleted orphaned spec: {checksum}")
                pruned_specs += 1

    return pruned_specs


def prune(mirror: Mirror, lockfile: Optional[str] = None) -> None:
    tty.debug(f"Pruning mirror: {mirror.fetch_url}")
    if lockfile is not None:
        tty.debug(f"Using lockfile: {lockfile}")
        _direct_prune(mirror, lockfile)

    orphaned_specs = _prune_orphaned_specs(mirror)

    tty.debug(f"Pruned {orphaned_specs} orphaned specs from mirror: {mirror.fetch_url}")

# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from typing import Any, Dict, List, Set
import json
from spack.util import s3
from tempfile import TemporaryFile

import llnl.util.tty as tty

from .mirrors.mirror import Mirror


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


def prune(mirror: Mirror) -> None:
    orphaned_specs = _prune_orphaned_specs(mirror)

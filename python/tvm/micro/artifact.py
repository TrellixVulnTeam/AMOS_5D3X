# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

""""Defines abstractions around compiler artifacts produced in compiling micro TVM binaries."""

import io
import os
import json
import shutil
import tarfile


class ArtifactFileNotFoundError(Exception):
    """Raised when an artifact file cannot be found on disk."""


class ArtifactBadSymlinkError(Exception):
    """Raised when an artifact symlink points outside the base directory."""


class ArtifactBadArchiveError(Exception):
    """Raised when an artifact archive is malformed."""


class Artifact:
    """Describes a compiler artifact and defines common logic to archive it for transport."""

    # A version number written to the archive.
    ENCODING_VERSION = 1

    # A unique string identifying the type of artifact in an archive. Subclasses must redefine this
    # variable.
    ARTIFACT_TYPE = None

    @classmethod
    def unarchive(cls, archive_path, base_dir):
        """Unarchive an artifact into base_dir.

        Parameters
        ----------
        archive_path : str
            Path to the archive file.
        base_dir : str
            Path to a non-existent, empty directory under which the artifact will live.

        Returns
        -------
        Artifact :
            The unarchived artifact.
        """
        if os.path.exists(base_dir):
            raise ValueError(f"base_dir exists: {base_dir}")

        base_dir_parent, base_dir_name = os.path.split(base_dir)
        temp_dir = os.path.join(base_dir_parent, f"__tvm__{base_dir_name}")
        os.mkdir(temp_dir)
        try:
            with tarfile.open(archive_path) as tar_f:
                def is_within_directory(directory, target):
                    
                    abs_directory = os.path.abspath(directory)
                    abs_target = os.path.abspath(target)
                
                    prefix = os.path.commonprefix([abs_directory, abs_target])
                    
                    return prefix == abs_directory
                
                def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
                
                    for member in tar.getmembers():
                        member_path = os.path.join(path, member.name)
                        if not is_within_directory(path, member_path):
                            raise Exception("Attempted Path Traversal in Tar File")
                
                    tar.extractall(path, members, numeric_owner=numeric_owner) 
                    
                
                safe_extract(tar_f, temp_dir)

                temp_dir_contents = os.listdir(temp_dir)
                if len(temp_dir_contents) != 1:
                    raise ArtifactBadArchiveError(
                        "Expected exactly 1 subdirectory at root of archive, got "
                        f"{temp_dir_contents!r}"
                    )

                metadata_path = os.path.join(temp_dir, temp_dir_contents[0], "metadata.json")
                if not metadata_path:
                    raise ArtifactBadArchiveError("No metadata.json found in archive")

                with open(metadata_path) as metadata_f:
                    metadata = json.load(metadata_f)

                version = metadata.get("version")
                if version != cls.ENCODING_VERSION:
                    raise ArtifactBadArchiveError(
                        f"archive version: expect {cls.EXPECTED_VERSION}, found {version}"
                    )

                os.rename(os.path.join(temp_dir, temp_dir_contents[0]), base_dir)

                artifact_cls = cls
                for sub_cls in cls.__subclasses__():
                    if sub_cls.ARTIFACT_TYPE is not None and sub_cls.ARTIFACT_TYPE == metadata.get(
                        "artifact_type"
                    ):
                        artifact_cls = sub_cls
                        break

                return artifact_cls.from_unarchived(
                    base_dir, metadata["labelled_files"], metadata["metadata"]
                )
        finally:
            shutil.rmtree(temp_dir)

    @classmethod
    def from_unarchived(cls, base_dir, labelled_files, metadata):
        return cls(base_dir, labelled_files, metadata)

    def __init__(self, base_dir, labelled_files, metadata):
        """Create a new artifact.

        Parameters
        ----------
        base_dir : str
            The path to a directory on disk which contains all the files in this artifact.
        labelled_files : Dict[str, str]
            A dict mapping a file label to the relative paths of the files that carry that label.
        metadata : Dict
            A dict containing artitrary JSON-serializable key-value data describing the artifact.
        """
        self.base_dir = os.path.realpath(base_dir)
        self.labelled_files = labelled_files
        self.metadata = metadata

        for label, files in labelled_files.items():
            for f in files:
                f_path = os.path.join(self.base_dir, f)
                if not os.path.lexists(f_path):
                    raise ArtifactFileNotFoundError(f"{f} (label {label}): not found at {f_path}")

                if os.path.islink(f_path):
                    link_path = os.path.readlink(f_path)
                    if os.path.isabs(link_path):
                        link_fullpath = link_path
                    else:
                        link_fullpath = os.path.join(os.path.dirname(f_path), link_path)

                    link_fullpath = os.path.realpath(link_fullpath)
                    if not link_fullpath.startswith(self.base_dir):
                        raise ArtifactBadSymlinkError(
                            f"{f} (label {label}): symlink points outside artifact tree"
                        )

    def abspath(self, rel_path):
        """Return absolute path to the member with the given relative path."""
        return os.path.join(self.base_dir, rel_path)

    def label(self, label):
        """Return a list of relative paths to files with the given label."""
        return self.labelled_files[label]

    def label_abspath(self, label):
        return [self.abspath(p) for p in self.labelled_files[label]]

    def archive(self, archive_path):
        """Create a relocatable tar archive of the artifacts.

        Parameters
        ----------
        archive_path : str
            Path to the tar file to create. Or, path to a directory, under which a tar file will be
            created named {base_dir}.tar.

        Returns
        -------
        str :
            The value of archive_path, after potentially making the computation describe above.
        """
        if os.path.isdir(archive_path):
            archive_path = os.path.join(archive_path, f"{os.path.basename(self.base_dir)}.tar")

        archive_name = os.path.splitext(os.path.basename(archive_path))[0]
        with tarfile.open(archive_path, "w") as tar_f:

            def _add_file(name, data, f_type):
                tar_info = tarfile.TarInfo(name=name)
                tar_info.type = f_type
                data_bytes = bytes(data, "utf-8")
                tar_info.size = len(data)
                tar_f.addfile(tar_info, io.BytesIO(data_bytes))

            _add_file(
                f"{archive_name}/metadata.json",
                json.dumps(
                    {
                        "version": self.ENCODING_VERSION,
                        "labelled_files": self.labelled_files,
                        "metadata": self.metadata,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                tarfile.REGTYPE,
            )
            for dir_path, _, files in os.walk(self.base_dir):
                for f in files:
                    file_path = os.path.join(dir_path, f)
                    archive_file_path = os.path.join(
                        archive_name, os.path.relpath(file_path, self.base_dir)
                    )
                    if not os.path.islink(file_path):
                        tar_f.add(file_path, archive_file_path, recursive=False)
                        continue

                    link_path = os.readlink(file_path)
                    if not os.path.isabs(link_path):
                        tar_f.add(file_path, archive_file_path, recursive=False)
                        continue

                    relpath = os.path.relpath(link_path, os.path.dirname(file_path))
                    _add_file(archive_file_path, relpath, tarfile.LNKTYPE)

        return archive_path

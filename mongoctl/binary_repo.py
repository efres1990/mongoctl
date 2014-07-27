__author__ = 'abdul'

import platform
import os
import sys
from utils import which, call_command
from errors import MongoctlException, FileNotInRepoError
from mongoctl_logging import log_info, log_verbose
from prompt import is_interactive_mode
from boto.s3.connection import S3Connection
from mongo_version import make_version_info, MongoEdition
import config
import urllib

VERSION_2_6_1 = make_version_info("2.6.1")

###############################################################################
# MongoDBBinaryRepository
###############################################################################
class MongoDBBinaryRepository(object):

    ###########################################################################
    # Constructor
    ###########################################################################
    def __init__(self):
        self._name = None
        self._supported_editions = None
        self._url_template = None

    ###########################################################################
    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, val):
        self._name = val

    ###########################################################################
    @property
    def supported_editions(self):
        return self._supported_editions

    @supported_editions.setter
    def supported_editions(self, val):
        self._supported_editions = val

    ###########################################################################
    @property
    def url_template(self):
        return self._url_template

    @url_template.setter
    def url_template(self, val):
        self._url_template = val

    ###########################################################################
    def download_file(self, mongodb_version, mongodb_edition,
                      destination=None):

        destination = destination or os.getcwd()
        bits = platform.architecture()[0].replace("bit", "")
        os_name = get_os_name()

        platform_spec = get_validate_platform_spec(os_name, bits)

        os_dist_name, os_dist_version = get_os_dist_info()

        return self.do_download_file(
            os_name, platform_spec, os_dist_name,
            os_dist_version,mongodb_version, mongodb_edition, destination)

    ###########################################################################
    def do_download_file(self, os_name, platform_spec, os_dist_name,
                         os_dist_version, mongodb_version, mongodb_edition,
                         destination):

        url = self.get_download_url(os_name, platform_spec, os_dist_name,
                                    os_dist_version, mongodb_version,
                                    mongodb_edition)

        response = urllib.urlopen(url)

        if response.code == 404:
            raise FileNotInRepoError("File not found in repo")
        if response.getcode() != 200:
            msg = ("Unable to download from url '%s' (response code '%s'). "
                   "It could be that version '%s' you specified does not exist."
                   " Please double check the version you provide" %
                   (url, response.getcode(), mongodb_version))
            raise MongoctlException(msg)

        return download_url(url, destination)

    ###########################################################################
    def get_download_url(self, os_name, platform_spec, os_dist_name,
                         os_dist_version, mongodb_version, mongodb_edition):
        if mongodb_edition not in self.supported_editions:
            msg = ("Edition '%s' not supported by MongoDBBinaryRepository '%s'"
                   % (mongodb_edition, self.name))
            raise Exception(msg)

        os_dist_version_no_dots = (os_dist_version and
                                   os_dist_version.replace('.', ''))
        kwargs = {
            "os_name": os_name,
            "platform_spec": platform_spec,
            "os_dist_name": os_dist_name,
            "os_dist_version": os_dist_version,
            "os_dist_version_no_dots": os_dist_version_no_dots,
            "mongodb_version": mongodb_version,
            "mongodb_edition": mongodb_edition
        }

        return self.url_template.format(**kwargs)


###############################################################################
# DefaultMongoDBBinaryRepository
###############################################################################
class DefaultMongoDBBinaryRepository(MongoDBBinaryRepository):

    ###########################################################################
    def __init__(self):
        MongoDBBinaryRepository.__init__(self)
        self.supported_editions = [MongoEdition.COMMUNITY,
                                   MongoEdition.ENTERPRISE]

    ###########################################################################
    def get_download_url(self, os_name, platform_spec, os_dist_name,
                        os_dist_version, mongodb_version, mongodb_edition):

        version_info = make_version_info(mongodb_version)
        if mongodb_edition == MongoEdition.COMMUNITY:
            archive_name = "mongodb-%s-%s.tgz" % (platform_spec,
                                                  mongodb_version)
            domain = "fastdl.mongodb.org"
        elif mongodb_edition == MongoEdition.ENTERPRISE:
            if version_info and version_info >= VERSION_2_6_1:
                domain = "downloads.10gen.com"
                rel_name = "enterprise"
            else:
                rel_name = "subscription"
                domain = "downloads.mongodb.com"

            archive_name = ("mongodb-%s-%s-%s%s-%s.tgz" %
                            (platform_spec, rel_name, os_dist_name,
                             os_dist_version.replace('.', ''),
                             mongodb_version))

        else:
            raise MongoctlException("Unsupported edition '%s'" %
                                    mongodb_edition)

        return "http://%s/%s/%s" % (domain, os_name, archive_name)

###############################################################################
# S3MongoDBBinaryRepository
###############################################################################
class S3MongoDBBinaryRepository(MongoDBBinaryRepository):

    ###########################################################################
    # Constructor
    ###########################################################################
    def __init__(self):
        MongoDBBinaryRepository.__init__(self)
        self._bucket_name = None
        self._access_key = None
        self._secret_key = None

        self._bucket = None

    ###########################################################################
    @property
    def bucket_name(self):
        return self._bucket_name

    @bucket_name.setter
    def bucket_name(self, bucket_name):
        self._bucket_name = str(bucket_name)

    ###########################################################################
    @property
    def bucket(self):

        if not self._bucket:
            conn = S3Connection(self.access_key, self.secret_key)
            self._bucket = conn.get_bucket(self.bucket_name)

        return self._bucket

    ###########################################################################
    @property
    def access_key(self):
        return self._access_key

    @access_key.setter
    def access_key(self, val):
        self._access_key = val

    ###########################################################################
    @property
    def secret_key(self):
        return self._secret_key

    @secret_key.setter
    def secret_key(self, val):
        self._secret_key = val

    ###########################################################################
    def do_download_file(self, os_name, platform_spec, os_dist_name,
                         os_dist_version, mongodb_version, mongodb_edition,
                         destination):

        file_path = self.get_download_url(os_name, platform_spec, os_dist_name,
                                          os_dist_version, mongodb_version,
                                          mongodb_edition)

        return self._download_file_from_bucket(file_path, destination)

    ###########################################################################
    def _download_file_from_bucket(self, file_path, destination):

        print("Downloading '%s' from s3 bucket '%s'" %
              (file_path, self.bucket_name))

        key = self.bucket.get_key(file_path)
        file_name = os.path.basename(file_path)

        if not key:
            raise FileNotInRepoError("No such file '%s' in bucket '%s'" %
                                     (file_path, self.bucket_name))

        destination_path = os.path.join(destination, file_name)
        file_obj = open(destination_path, mode="w")

        num_call_backs = key.size / 1000
        key.get_contents_to_file(file_obj, cb=_download_progress,
                                 num_cb=num_call_backs)

        print("Download completed successfully!!")

        return destination_path




###############################################################################
_registered_repos = list()

def get_registered_binary_repositories():
    global _registered_repos
    if _registered_repos:
        return _registered_repos


    _registered_repos.append(DefaultMongoDBBinaryRepository())

    repo_configs = config.get_mongoctl_config().get("customBinaryRepositories")

    if repo_configs:
        for name, repo_config in repo_configs.items():
            binary_repo = _make_binary_repo(name, repo_config)
            _registered_repos.append(binary_repo)
    return _registered_repos


###########################################################################
def download_mongodb_binary(mongodb_version, mongodb_edition,
                            destination=None):
    destination = destination or os.getcwd()

    for repo in get_registered_binary_repositories():
        if mongodb_edition in repo.supported_editions:
            try:
                return repo.download_file(mongodb_version, mongodb_edition,
                                          destination=destination)
            except FileNotInRepoError, e:
                log_verbose("No mongodb binary (version: '%s', edition: '%s' )"
                            "found in repo '%s'" %
                            (mongodb_version, mongodb_edition, repo.name))

    raise MongoctlException("No mongodb binary (version: '%s', edition: '%s')"
                            % (mongodb_version, mongodb_edition))

###############################################################################
# HELPERS
###############################################################################

def _make_binary_repo(name, repo_config):
    repo_type = repo_config.get("_type")
    if repo_type == "s3":
        repo = S3MongoDBBinaryRepository()
        repo.bucket_name = repo_config["bucketName"]
        repo.access_key = repo_config["accessKey"]
        repo.secret_key = repo_config["secretKey"]
    else:
        repo = MongoDBBinaryRepository()

    repo.name = name
    repo.url_template = repo_config["urlTemplate"]
    repo.supported_editions = repo_config["supportedEditions"]

    return repo

###############################################################################
def _download_progress(transferred, size):
    percentage = (float(transferred)/float(size)) * 100
    sys.stdout.write("\rDownloaded %s bytes of %s. %%%i "
                     "completed" %
                     (transferred, size, percentage))
    sys.stdout.flush()

###############################################################################
def get_validate_platform_spec(os_name, bits):

    if os_name not in ["linux", "osx", "win32", "sunos5"]:
        raise Exception("Unsupported OS %s" % os_name)

    if bits == "64":
        return "%s-x86_64" % os_name
    else:
        if os_name == "linux":
            return "linux-i686"
        elif os_name in ["osx" , "win32"]:
            return "%s-i386" % os_name
        elif os_name == "sunos5":
            return "i86pc"

###############################################################################
def get_os_dist_info():
    """
        Returns the distribution info
    """

    distribution = platform.dist()
    dist_name = distribution[0].lower()
    dist_version_str = distribution[1]
    if dist_name and dist_version_str:
        return dist_name, dist_version_str
    else:
        return None, None

###############################################################################
def get_os_name():
    os_name = platform.system().lower()

    if os_name == 'darwin' and platform.mac_ver():
        os_name = "osx"

    return os_name


###############################################################################
def download_url(url, destination=None):
    destination = destination or os.getcwd()

    log_info("Downloading %s..." % url)

    if which("curl"):
        download_cmd = ['curl', '-O']
        if not is_interactive_mode():
            download_cmd.append('-Ss')
    elif which("wget"):
        download_cmd = ['wget']
    else:
        msg = ("Cannot download file.You need to have 'curl' or 'wget"
               "' command in your path in order to proceed.")
        raise MongoctlException(msg)

    download_cmd.append(url)
    call_command(download_cmd, cwd=destination)
    archive_name = url.split("/")[-1]
    return os.path.join(destination, archive_name)